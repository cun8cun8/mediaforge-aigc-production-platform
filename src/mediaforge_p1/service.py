from __future__ import annotations

import base64
import copy
import csv
import hashlib
import io
import json
import math
import mimetypes
import os
import re
import sqlite3
from time import perf_counter
import zipfile
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import RLock
from time import sleep
from typing import Any
from uuid import uuid4

from .contracts import (
    AdaptationScene,
    AdaptationSceneInput,
    Artifact,
    Capability,
    ControlNet,
    CreativeBrief,
    DialogueLine,
    GenerationSpec,
    Intent,
    JobStatus,
    NarrativeCandidateStatus,
    NarrativeEvent,
    NarrativeEventCandidate,
    NarrativeEventCandidateProposal,
    NarrativeEventInput,
    NarrativeSourceChapter,
    NarrativeSourceChapterInput,
    NarrativeSourceDocument,
    ProjectStatus,
    ProviderConstraints,
    QualityRequirements,
    ReferenceAssetRef,
    ReviewStatus,
    ShotCard,
    WorkflowSpec,
)
from .jobs import JobLeasePolicy, JobRecord, JobStore, RetryPolicy
from .llm import StoryPlan, StoryPlanner, StoryPlannerError
from .media import (
    SubtitleCue,
    concat_videos,
    create_video_from_image,
    create_media_derivatives,
    inspect_image_visual_signal,
    probe_image,
    probe_audio,
    probe_video,
    sha256_file,
    mix_audio,
    render_timed_audio,
    write_srt,
)
from .policy import SafetyPolicy
from .providers import GenerationProvider, MockProvider
from .registry import LicenseRegistry
from .registry_sync import RegistrySyncError, fetch_registry, validate_sync_url
from .router import ProviderRegistration, ProviderRouter, ProviderRoutingError
from .quotas import QuotaPolicy, QuotaViolation
from .webhooks import WebhookDispatcher
from .audit_integrity import event_hash as audit_event_hash, verify_event_chain
from .audit_anchor import AuditAnchorError, AuditAnchorStore
from .observability import RuntimeMetrics
from .alerts import (
    OperationsAlertSettings,
    evaluate_operations_alerts,
    operations_alerts_prometheus,
)
from .delivery import DeliveryDispatchError, DeliveryDispatcher
from .enterprise_runtime import (
    ControlPlaneUnavailable,
    EnterpriseConfigurationError,
    EnterpriseRuntime,
)
from .quality import QualityEvaluator
from .visual import visual_report
from .speech import SpeechSynthesizer, SpeechSynthesisError
from .lipsync import LipSyncError, build_lipsync_from_env
from .memory import MemorySettings, build_memory_store
from .planning import PlanningWorkflow, PlanningError, fingerprint
from .production_workflow import PRODUCTION_STAGES, downstream_stages, stage_definition, stage_index
from .llmops import (
    PromptValidationError,
    activate_prompt_version,
    build_prompt_version,
    default_prompt_versions,
    prompt_registry_view,
    render_prompt,
)
from .content_credentials import ContentCredentialError, ContentCredentials
from .collaboration import (
    CollaborationDocumentError,
    apply_operations as apply_collaboration_operations,
    document_text as collaboration_document_text,
    empty_document as empty_collaboration_document,
    replacement_operations as collaboration_replacement_operations,
)
from .source_ingest import (
    MAX_SOURCE_DOCUMENT_BYTES,
    PARSER_VERSION,
    SourceIngestError,
    ocr_status,
    parse_source_document,
)


class ProjectNotFound(KeyError):
    pass


class ShotNotFound(KeyError):
    pass


class WorkflowError(ValueError):
    pass


class TenantViolation(WorkflowError):
    pass


class PolicyViolation(WorkflowError):
    def __init__(self, message: str, report: dict[str, Any]) -> None:
        super().__init__(message)
        self.report = report


class JobNotFound(KeyError):
    pass


class DeliveryNotFound(KeyError):
    pass


@dataclass
class ReviewRecord:
    status: ReviewStatus
    comment: str
    actor: str
    occurred_at: datetime


@dataclass
class AuditEvent:
    action: str
    actor: str
    message: str
    occurred_at: datetime
    shot_id: str | None = None
    details: dict[str, Any] = field(default_factory=dict)
    trace_id: str | None = None
    sequence: int | None = None
    previous_hash: str | None = None
    event_hash: str | None = None


@dataclass
class ShotRuntime:
    shot: ShotCard
    spec: GenerationSpec
    review_status: ReviewStatus = ReviewStatus.PENDING
    revision: int = 0
    current_job_id: str | None = None
    current_artifact: dict[str, Any] | None = None
    route: dict[str, Any] | None = None
    quality: dict[str, Any] | None = None
    artifact_history: list[dict[str, Any]] = field(default_factory=list)
    variants: list[dict[str, Any]] = field(default_factory=list)
    reviews: list[ReviewRecord] = field(default_factory=list)


@dataclass
class ProjectRuntime:
    brief: CreativeBrief
    trace_id: str = field(default_factory=lambda: f"trace_{uuid4().hex[:16]}")
    story_bible: dict[str, Any] | None = None
    source_documents: list[NarrativeSourceDocument] = field(default_factory=list)
    source_chapters: list[NarrativeSourceChapter] = field(default_factory=list)
    narrative_event_candidates: list[NarrativeEventCandidate] = field(default_factory=list)
    narrative_events: list[NarrativeEvent] = field(default_factory=list)
    adaptation_scenes: list[AdaptationScene] = field(default_factory=list)
    shots: dict[str, ShotRuntime] = field(default_factory=dict)
    status: ProjectStatus = ProjectStatus.DRAFT
    created_at: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    final_mp4: str | None = None
    lipsync_artifact: dict[str, Any] | None = None
    subtitle_srt: str | None = None
    audio_track: str | None = None
    audio_track_metadata: dict[str, Any] | None = None
    dialogue_timeline: dict[str, Any] | None = None
    delivery_package: str | None = None
    archive_package: str | None = None
    release: dict[str, Any] | None = None
    archived_at: datetime | None = None
    policy_reports: list[dict[str, Any]] = field(default_factory=list)
    audit_events: list[AuditEvent] = field(default_factory=list)
    audit_chain_origin: str = "native"
    import_evidence: dict[str, Any] | None = None
    audit_anchors: list[dict[str, Any]] = field(default_factory=list)
    evaluations: list[dict[str, Any]] = field(default_factory=list)
    evaluation_baselines: list[dict[str, Any]] = field(default_factory=list)
    provider_benchmarks: list[dict[str, Any]] = field(default_factory=list)
    comparison_reports: list[dict[str, Any]] = field(default_factory=list)
    deliveries: list[dict[str, Any]] = field(default_factory=list)
    delivery_feedback: list[dict[str, Any]] = field(default_factory=list)
    reference_assets: list[dict[str, Any]] = field(default_factory=list)
    members: list[dict[str, Any]] = field(default_factory=list)
    comments: list[dict[str, Any]] = field(default_factory=list)
    prompt_versions: list[dict[str, Any]] = field(default_factory=list)
    evaluation_annotations: list[dict[str, Any]] = field(default_factory=list)
    experiments: list[dict[str, Any]] = field(default_factory=list)
    media_derivatives: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    edit_timeline: dict[str, Any] | None = None
    collaboration_presence: list[dict[str, Any]] = field(default_factory=list)
    edit_locks: list[dict[str, Any]] = field(default_factory=list)
    collaboration_documents: dict[str, dict[str, Any]] = field(default_factory=dict)
    collaboration_events: list[dict[str, Any]] = field(default_factory=list)
    collaboration_event_sequence: int = 0
    content_credentials: list[dict[str, Any]] = field(default_factory=list)
    stage_locks: dict[str, dict[str, Any]] = field(default_factory=dict)
    stage_invalidations: list[dict[str, Any]] = field(default_factory=list)


class MediaForgeService:
    """P0 application service for the complete short-drama loop."""

    def __init__(
        self,
        output_root: Path,
        provider: GenerationProvider | None = None,
        license_registry: LicenseRegistry | None = None,
        story_planner: StoryPlanner | None = None,
        provider_registrations: list[ProviderRegistration] | None = None,
    ) -> None:
        self.output_root = output_root
        self.output_root.mkdir(parents=True, exist_ok=True)
        self.state_path = self.output_root / "mediaforge-state.json"
        self.state_database_path = self.output_root / "mediaforge-state.sqlite3"
        self.state_backend = os.getenv("MEDIAFORGE_STATE_BACKEND", "json").strip().lower()
        if self.state_backend not in {"json", "sqlite", "postgres"}:
            raise WorkflowError("MEDIAFORGE_STATE_BACKEND must be json, sqlite or postgres")
        self._state_lock = RLock()
        self._control_plane_lock = RLock()
        self.projects: dict[str, ProjectRuntime] = {}
        self.workers: dict[str, dict[str, Any]] = {}
        self.retry_policy = RetryPolicy.from_env()
        self.job_lease_policy = JobLeasePolicy.from_env()
        self.jobs = JobStore(max_attempts=self.retry_policy.max_attempts)
        self.policy = SafetyPolicy()
        self.quota_policy = QuotaPolicy.from_env()
        self.provider = provider or MockProvider()
        self.story_planner = story_planner
        self.webhooks = WebhookDispatcher.from_env()
        self.webhooks.configure_outbox(self.output_root / "webhook-outbox.json")
        self.siem = WebhookDispatcher.from_siem_env()
        self.siem.configure_outbox(self.output_root / "siem-outbox.json")
        self.runtime_metrics = RuntimeMetrics()
        self.operations_alert_settings = OperationsAlertSettings.from_env()
        self._production_readiness_cache: dict[str, Any] | None = None
        self.delivery_dispatcher = DeliveryDispatcher.from_env(self.output_root)
        self.quality_evaluator = QualityEvaluator.from_env()
        self.speech_synthesizer = SpeechSynthesizer.from_env()
        self.lipsync = build_lipsync_from_env()
        self.memory_settings = MemorySettings.from_env()
        self.story_memory = build_memory_store(self.output_root / "story-memory.sqlite3", self.memory_settings)
        self.enterprise = EnterpriseRuntime(self.output_root)
        self.audit_anchors = AuditAnchorStore(
            self.output_root,
            self.enterprise.storage,
        )
        self.content_credentials = ContentCredentials()
        self.license_registry = license_registry or LicenseRegistry.from_env()
        self.license_registry_metadata: dict[str, Any] = {
            "updated_at": None,
            "updated_by": None,
            "change_id": None,
        }
        self.provider_status = {
            "mode": "custom" if provider else "mock",
            "provider": self.provider.name,
            "configured": True,
            "message": "Provider is configured.",
            "capabilities": [],
        }
        self.router = ProviderRouter(
            provider_registrations
            or [ProviderRegistration(provider=self.provider, priority=1)]
        )
        self.provider_statuses: list[dict[str, Any]] = []
        self._load_state()
        self.planning = PlanningWorkflow(self)

    def set_provider_status(
        self,
        status: dict[str, Any],
        statuses: list[dict[str, Any]] | None = None,
    ) -> None:
        self.provider_status = copy.deepcopy(status)
        if statuses is not None:
            self.provider_statuses = copy.deepcopy(statuses)
        elif not self.provider_statuses:
            self.provider_statuses = [copy.deepcopy(status)]

    def storage_status(self) -> dict[str, Any]:
        if self.state_backend == "postgres":
            status = self.enterprise.postgres.status_view()
            return {**status, "ready": status["configured"] and status["driver_ready"], "durable": True}
        path = self.state_database_path if self.state_backend == "sqlite" else self.state_path
        return {
            "backend": self.state_backend,
            "path": str(path),
            "ready": path.parent.is_dir(),
            "durable": True,
        }

    def planner_status(self) -> dict[str, Any]:
        if self.story_planner is None:
            return {
                "mode": "deterministic",
                "name": "built-in-story-planner",
                "configured": True,
                "message": "本地确定性规划器已启用。",
            }
        return self.story_planner.status_view()

    def webhook_status(self) -> dict[str, Any]:
        return self.webhooks.status_view()

    def siem_status(self) -> dict[str, Any]:
        return self.siem.status_view()

    def audit_anchor_status(self) -> dict[str, Any]:
        return self.audit_anchors.status_view()

    def runtime_metrics_view(self) -> dict[str, Any]:
        return self.runtime_metrics.snapshot()

    def delivery_dispatch_status(self) -> dict[str, Any]:
        return self.delivery_dispatcher.status_view()

    def quality_evaluation_status(self) -> dict[str, Any]:
        return {**self.quality_evaluator.status_view(), "local_visual": "sampled-visual-v1",
                "visual_gate": os.getenv("MEDIAFORGE_VISUAL_GATE", "false").lower() in {"true", "1"}}

    def lipsync_status(self) -> dict[str, Any]:
        return self.lipsync.status_view()

    def operations_alerts(self) -> dict[str, Any]:
        """Evaluate operator-facing alerts from current persisted state and metrics."""
        cached_readiness = self._production_readiness_cache
        readiness = (
            cached_readiness
            if self.operations_alert_settings.require_production_ready
            and isinstance(cached_readiness, dict)
            else {"production_ready": not self.operations_alert_settings.require_production_ready}
        )
        return evaluate_operations_alerts(
            settings=self.operations_alert_settings,
            studio_metrics=self.studio_metrics(),
            runtime_metrics=self.runtime_metrics_view(),
            workers=self.worker_status(),
            production_readiness=readiness,
        )

    def operations_alerts_prometheus(self) -> str:
        return operations_alerts_prometheus(self.operations_alerts())

    def source_ingest_status(self) -> dict[str, Any]:
        return ocr_status()

    def enterprise_status(self) -> dict[str, Any]:
        status = self.enterprise.status_view()
        status["story_memory"] = self.story_memory_status()
        status["webhook"] = self.webhook_status()
        status["siem"] = self.siem_status()
        status["audit_anchor"] = self.audit_anchor_status()
        return status

    def control_plane_status(self, *, acquire: bool = False) -> dict[str, Any]:
        """Return traffic readiness, optionally acquiring the active/passive lease."""
        control_plane = self.enterprise.control_plane
        if not acquire:
            return control_plane.status_view()
        with self._control_plane_lock:
            was_primary = control_plane.primary
            primary = control_plane.acquire_or_renew()
            if primary and control_plane.enabled and not was_primary:
                self._reload_shared_state()
            return control_plane.status_view()

    @contextmanager
    def control_plane_mutation_guard(self):
        """Serialize writes locally and fence a leased standby before mutation."""
        control_plane = self.enterprise.control_plane
        if not control_plane.enabled:
            yield
            return
        with self._control_plane_lock:
            was_primary = control_plane.primary
            if not control_plane.acquire_or_renew():
                raise ControlPlaneUnavailable(
                    "this control plane is standby; retry through a healthy primary instance"
                )
            if not was_primary:
                self._reload_shared_state()
            yield

    def release_control_plane(self) -> None:
        self.enterprise.control_plane.release()

    def _reload_shared_state(self) -> None:
        """Discard standby memory before it becomes the PostgreSQL snapshot writer."""
        self.projects = {}
        self.workers = {}
        self.jobs = JobStore(max_attempts=self.retry_policy.max_attempts)
        self.license_registry = LicenseRegistry.from_env()
        self.license_registry_metadata = {
            "updated_at": None,
            "updated_by": None,
            "change_id": None,
        }
        self._load_state()

    def story_memory_status(self) -> dict[str, Any]:
        return {
            **self.story_memory.status_view(),
            "enabled": self.memory_settings.enabled,
            "top_k": self.memory_settings.top_k,
            "max_context_chars": self.memory_settings.max_context_chars,
            "planner_supports_context": callable(getattr(self.story_planner, "plan_with_context", None)),
        }

    def story_memory_sources(self, project_id: str, *, subject: str | None = None) -> dict[str, Any]:
        project = self._project(project_id)
        return {"settings": self.story_memory_status(), "sources": [
            {"project_id": key, "title": candidate.brief.title, "archived": candidate.archived_at is not None}
            for key, candidate in self.projects.items()
            if candidate.brief.tenant_id == project.brief.tenant_id
            and (candidate.shots or key == project_id)
            and (subject is None or self.project_member_role(key, subject) in self.project_role_levels())
        ]}

    def _sync_story_memory(self, project: ProjectRuntime) -> None:
        documents = self._story_memory_documents(project)
        self.story_memory.replace_project(tenant_id=project.brief.tenant_id,
                                          project_id=project.brief.project_id, documents=documents)

    def _story_memory_documents(self, project: ProjectRuntime) -> list[dict[str, Any]]:
        documents = []
        # The index is a cache rebuilt from saved, accepted plans, not previous LLM attempts.
        if project.shots and project.story_bible:
            bible = {key: value for key, value in project.story_bible.items()
                     if key not in {"memory_retrieval", "planning_run_id", "planning_graph_version", "planning_trace"}}
            documents.append({"kind": "story_bible", "content": json.dumps(bible, ensure_ascii=False),
                              "metadata": {"title": project.brief.title}})
            for runtime in project.shots.values():
                shot = runtime.shot
                if not self.policy.assess_shot(shot, runtime.spec).passed:
                    continue
                documents.append({"kind": "shot_card",
                                  "content": f"{shot.scene} {shot.description} {' '.join(shot.characters)} {shot.subtitle_text or ''}",
                                  "metadata": {"title": project.brief.title, "shot_id": shot.shot_id, "revision": runtime.revision}})
            for asset in project.reference_assets:
                documents.append({"kind": "reference_asset",
                                  "content": f"{asset.get('name', '')} {asset.get('character', '')} {asset.get('kind', '')}",
                                  "metadata": {key: asset.get(key) for key in ("asset_id", "sha256", "license")}})
        return documents

    def sync_story_memory_to_ragflow(self, project_id: str, *, actor: str) -> dict[str, Any]:
        project = self._project(project_id)
        self._ensure_active(project)
        sync_project = getattr(self.story_memory, "sync_project", None)
        if not callable(sync_project):
            raise WorkflowError("RAGFlow write sync requires MEDIAFORGE_RAG_BACKEND=ragflow")
        result = sync_project(
            tenant_id=project.brief.tenant_id,
            project_id=project_id,
            documents=self._story_memory_documents(project),
        )
        self._record_event(
            project,
            action="memory.ragflow_snapshot_synced",
            actor=actor,
            message="Approved story snapshot synchronized to RAGFlow.",
            details={
                key: result[key]
                for key in ("document_name", "content_sha256", "content_bytes", "uploaded_count", "existing_count")
            },
        )
        self._persist()
        return result

    def story_memory_search(
        self, project_id: str, query: str, limit: int | None = None, *,
        source_project_ids: list[str] | None = None, subject: str | None = None,
    ) -> dict[str, Any]:
        project = self._project(project_id)
        sources = list(dict.fromkeys(source_project_ids or [project_id]))
        if len(sources) > 20:
            raise WorkflowError("at most 20 memory source projects are allowed")
        candidates = []
        for source_id in sources:
            candidate = self.projects.get(source_id)
            if (candidate is None or candidate.brief.tenant_id != project.brief.tenant_id
                    or (subject is not None and self.project_member_role(source_id, subject) not in self.project_role_levels())):
                raise ProjectNotFound("memory source project not found")
            candidates.append(candidate)
        for candidate in candidates:
            self._sync_story_memory(candidate)
        results = self.story_memory.search(
            tenant_id=project.brief.tenant_id, project_ids=sources, query=query,
            limit=limit or self.memory_settings.top_k, max_context_chars=self.memory_settings.max_context_chars,
        )
        return {"schema_version": "mediaforge-story-memory-v2", "project_id": project_id,
                "query": query, "source_project_ids": sources, "results": results,
                "settings": self.story_memory_status()}

    def production_readiness(
        self,
        *,
        callback_configured: bool | None = None,
    ) -> dict[str, Any]:
        provider = self.provider_diagnostics()
        provider_statuses = provider["status"].get("providers") or [
            provider["status"]
        ]
        real_provider = any(
            item.get("mode") not in {"mock", "unknown", ""}
            for item in provider_statuses
        )
        callback_ready = (
            bool(callback_configured)
            if callback_configured is not None
            else bool(provider.get("callback_security", {}).get("configured"))
        )
        delivery = self.delivery_dispatch_status()
        storage = self.storage_status()
        planner = self.planner_status()
        staged_planning = self.planning.status_view()
        source_ingest = self.source_ingest_status()
        webhook = self.webhook_status()
        enterprise = self.enterprise_status()
        control_plane = self.control_plane_status(
            acquire=self.enterprise.control_plane.enabled
        )
        content_credentials = self.content_credentials_status()
        content_credentials_required = self.release_content_credentials_required()
        enterprise_ready = (
            bool(enterprise["identity"]["production_ready"])
            and bool(enterprise["storage"]["write_enabled"])
            and bool(enterprise["storage"]["connectivity_verified"])
            and bool(enterprise["queue"]["production_ready"])
            and bool(enterprise["database"]["configured"])
            and bool(enterprise["database"].get("connectivity_verified", True))
        )
        checks = [
            {
                "code": "control_plane",
                "passed": bool(control_plane.get("ready_for_traffic")),
                "blocking": bool(control_plane.get("enabled")),
                "message": (
                    "This instance holds the active control-plane lease."
                    if control_plane.get("ready_for_traffic")
                    else "This instance is not the active control-plane writer."
                ),
            },
            {
                "code": "story_memory",
                "passed": self.memory_settings.backend != "pgvector" or bool(
                    enterprise["story_memory"].get("allow_data_export")
                    and enterprise["story_memory"].get("connectivity_verified")
                    and enterprise["story_memory"].get("embedding_verified")
                ),
                "blocking": self.memory_settings.enabled and bool(enterprise["story_memory"]["planner_supports_context"]),
                "message": "Semantic retrieval requires a verified database and embedding service.",
            },
            {
                "code": "provider",
                "passed": bool(provider.get("production_ready", True)),
                "blocking": real_provider,
                "message": (
                    "Provider pool is ready."
                    if provider.get("production_ready", True)
                    else "Provider pool needs configuration or health checks."
                ),
            },
            {
                "code": "callback_security",
                "passed": callback_ready or not real_provider,
                "blocking": real_provider,
                "message": (
                    "HMAC callback verification is configured."
                    if callback_ready
                    else "Production Provider callbacks need MEDIAFORGE_CALLBACK_SECRET."
                ),
            },
            {
                "code": "story_planner",
                "passed": bool(planner.get("configured")),
                "blocking": False,
                "message": planner.get("message") or "Story planner is available.",
            },
            {
                "code": "source_ingest",
                "passed": source_ingest.get("configuration_error") is None,
                "blocking": False,
                "message": (
                    "Scan-PDF OCR configuration is valid."
                    if source_ingest.get("configuration_error") is None
                    else "Scan-PDF OCR configuration is invalid."
                ),
            },
            {
                "code": "staged_planning_checkpoint",
                "passed": (
                    not staged_planning.get("enabled")
                    or staged_planning.get("checkpoint_backend") == "sqlite"
                    or bool(staged_planning.get("checkpoint_connectivity_verified"))
                ),
                "blocking": bool(
                    staged_planning.get("enabled")
                    and staged_planning.get("checkpoint_backend") == "postgres"
                ),
                "message": (
                    "Shared PostgreSQL planning checkpoint was verified."
                    if staged_planning.get("checkpoint_connectivity_verified")
                    else "Run the administrator planning checkpoint probe before using shared planning."
                ),
            },
            {
                "code": "storage",
                "passed": bool(storage.get("ready")),
                "blocking": False,
                "message": (
                    f"State backend: {storage.get('backend')}."
                    " Use a single control plane with remote Workers."
                ),
            },
            {
                "code": "delivery",
                "passed": bool(delivery.get("configured")),
                "blocking": True,
                "message": (
                    "Delivery dispatcher is configured."
                    if delivery.get("configured")
                    else "Delivery dispatcher is disabled."
                ),
            },
            {
                "code": "quality_evaluator",
                "passed": bool(
                    not self.quality_evaluator.status_view().get("configured")
                    or self.quality_evaluator.status_view().get("enabled")
                ),
                "blocking": False,
                "message": (
                    "External quality evaluator is enabled."
                    if self.quality_evaluator.status_view().get("enabled")
                    else (
                        "External quality evaluator is disabled; local quality "
                        "gates remain active."
                    )
                ),
            },
            {
                "code": "webhook_outbox",
                "passed": bool(webhook.get("outbox_configured")),
                "blocking": False,
                "message": (
                    "Webhook outbox persistence is configured."
                    if webhook.get("outbox_configured")
                    else "Webhook outbox persistence is unavailable."
                ),
            },
            {
                "code": "runtime_metrics",
                "passed": True,
                "blocking": False,
                "message": "Runtime metrics endpoint is available.",
            },
            {
                "code": "enterprise_runtime",
                "passed": enterprise_ready,
                "blocking": False,
                "message": (
                    "Enterprise runtime adapters are configured."
                    if enterprise_ready
                    else "Enterprise runtime needs identity, storage or shared queue configuration."
                ),
            },
            {
                "code": "content_credentials",
                "passed": bool(content_credentials.get("production_ready")),
                "blocking": content_credentials_required,
                "message": (
                    "Trusted C2PA signing and independent verification are ready."
                    if content_credentials.get("production_ready")
                    else "Production release credentials require a trusted C2PA signer, verifier, and operator attestation."
                ),
            },
        ]
        blocking_failures = [
            check["code"]
            for check in checks
            if check["blocking"] and not check["passed"]
        ]
        warnings = [
            check["code"]
            for check in checks
            if not check["blocking"] and not check["passed"]
        ]
        next_actions = []
        if "provider" in blocking_failures:
            next_actions.append("Configure and health-check at least one production Provider.")
        if "callback_security" in blocking_failures:
            next_actions.append("Set MEDIAFORGE_CALLBACK_SECRET for Provider callbacks.")
        if "staged_planning_checkpoint" in blocking_failures:
            next_actions.append("Run POST /planning/probe with an administrator after configuring PostgreSQL planning checkpoints.")
        if "storage" in warnings:
            next_actions.append("Use MEDIAFORGE_STATE_BACKEND=sqlite for production state.")
        if not real_provider:
            next_actions.append("Run a real Provider smoke test before commercial launch.")
        if not content_credentials.get("production_ready"):
            next_actions.append(
                "Configure and independently verify trusted C2PA credentials before production release."
            )
        production_gaps = [
            {
                "code": check["code"],
                "severity": "BLOCKER" if check["blocking"] else "WARNING",
                "message": check["message"],
            }
            for check in checks
            if not check["passed"]
        ]
        report = {
            "schema_version": "mediaforge-production-readiness-v1",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "ready": not blocking_failures,
            "production_ready": real_provider and not blocking_failures and enterprise_ready,
            "grade": "READY" if not blocking_failures else "BLOCKED",
            "blocking_failures": blocking_failures,
            "warnings": warnings,
            "checks": checks,
            "production_gaps": production_gaps,
            "next_actions": next_actions,
            "provider": provider,
            "planner": planner,
            "staged_planning": staged_planning,
            "storage": storage,
            "delivery": delivery,
            "quality_evaluator": self.quality_evaluation_status(),
            "webhooks": webhook,
            "runtime_metrics": self.runtime_metrics_view(),
            "enterprise": enterprise,
            "control_plane": control_plane,
        }
        self._production_readiness_cache = copy.deepcopy(report)
        return report

    def worker_status(self, *, tenant_id: str | None = None) -> dict[str, Any]:
        now = datetime.now(timezone.utc)
        rows = []
        for worker_id, worker in sorted(self.workers.items()):
            if tenant_id is not None and worker.get("tenant_id") != tenant_id:
                continue
            item = copy.deepcopy(worker)
            heartbeat = item.get("last_heartbeat_at")
            if heartbeat:
                try:
                    heartbeat_at = datetime.fromisoformat(str(heartbeat))
                    if heartbeat_at.tzinfo is None:
                        heartbeat_at = heartbeat_at.replace(tzinfo=timezone.utc)
                    item["heartbeat_age_seconds"] = round(
                        max((now - heartbeat_at).total_seconds(), 0.0),
                        3,
                    )
                except ValueError:
                    item["heartbeat_age_seconds"] = None
                if (
                    item.get("status") == "ONLINE"
                    and item.get("heartbeat_age_seconds") is not None
                    and item["heartbeat_age_seconds"] > self.job_lease_policy.stale_after_seconds
                ):
                    item["status"] = "STALE"
            item["gpu"] = self._worker_gpu_summary(item.get("resources") or {})
            item["active_job_ids"] = [
                job.job_id
                for job in self.jobs.all()
                if job.worker_id == worker_id
                and job.status in {JobStatus.ADMITTED, JobStatus.RUNNING}
            ]
            rows.append(item)
        active_leases = [
            self._job_view(job)
            for job in self.jobs.all()
            if job.worker_id
            and (tenant_id is None or self.workers.get(job.worker_id, {}).get("tenant_id") == tenant_id)
            and job.status in {JobStatus.ADMITTED, JobStatus.RUNNING}
        ]
        return {
            "schema_version": "mediaforge-worker-control-v1",
            "generated_at": now.isoformat(),
            "lease_seconds": self.job_lease_policy.stale_after_seconds,
            "worker_count": len(rows),
            "online_count": sum(row.get("status") == "ONLINE" for row in rows),
            "stale_count": sum(row.get("status") == "STALE" for row in rows),
            "gpu_worker_count": sum(row["gpu"]["gpu_count"] > 0 for row in rows),
            "gpu_ready_count": sum(
                row.get("status") == "ONLINE" and row["gpu"]["available"]
                for row in rows
            ),
            "execution_mode": "api-provider-dispatch",
            "active_lease_count": len(active_leases),
            "workers": rows,
            "active_leases": active_leases,
        }

    @staticmethod
    def _worker_gpu_summary(resources: dict[str, Any]) -> dict[str, Any]:
        """Normalize optional GPU telemetry without trusting it as execution proof."""
        probe = resources.get("gpu_probe")
        probe = probe if isinstance(probe, dict) else {}
        raw_gpus = resources.get("gpus")
        raw_gpus = raw_gpus if isinstance(raw_gpus, list) else []

        def nonnegative(value: Any) -> int | None:
            try:
                parsed = float(value)
            except (TypeError, ValueError):
                return None
            return int(parsed) if math.isfinite(parsed) and parsed >= 0 else None

        gpus: list[dict[str, Any]] = []
        for position, raw in enumerate(raw_gpus[:16]):
            if not isinstance(raw, dict):
                continue
            total = nonnegative(raw.get("memory_total_mib"))
            free = nonnegative(raw.get("memory_free_mib"))
            if total is None and free is None:
                continue
            gpus.append(
                {
                    "index": nonnegative(raw.get("index")) if raw.get("index") is not None else position,
                    "name": str(raw.get("name") or "GPU")[:240],
                    "driver_version": str(raw.get("driver_version") or "")[:120] or None,
                    "memory_total_mib": total or 0,
                    "memory_free_mib": min(free or 0, total) if total is not None else free or 0,
                    "utilization_percent": nonnegative(raw.get("utilization_percent")),
                }
            )
        if not gpus:
            total = nonnegative(resources.get("gpu_memory_total_mib"))
            free = nonnegative(resources.get("gpu_memory_free_mib"))
            if total is not None or free is not None:
                gpus = [{
                    "index": 0,
                    "name": str(resources.get("gpu_name") or "GPU")[:240],
                    "driver_version": None,
                    "memory_total_mib": total or 0,
                    "memory_free_mib": min(free or 0, total) if total is not None else free or 0,
                    "utilization_percent": None,
                }]
        status = str(probe.get("status") or ("available" if gpus else "unavailable")).lower()
        available = bool(gpus) and status == "available" and sum(item["memory_free_mib"] for item in gpus) > 0
        return {
            "available": available,
            "probe_status": status,
            "probe_source": str(probe.get("source") or "worker-declared")[:120],
            "probe_message": str(probe.get("message") or "")[:500] or None,
            "gpu_count": len(gpus),
            "memory_total_mib": sum(item["memory_total_mib"] for item in gpus),
            "memory_free_mib": sum(item["memory_free_mib"] for item in gpus),
            "gpus": gpus,
            "execution_mode": str(resources.get("execution_mode") or "api-provider-dispatch")[:120],
        }

    def _worker_gpu_admission(
        self, worker: dict[str, Any], minimum_gpu_memory_mib: int
    ) -> dict[str, Any]:
        gpu = self._worker_gpu_summary(worker.get("resources") or {})
        if minimum_gpu_memory_mib <= 0:
            return {"eligible": True, "minimum_gpu_memory_mib": 0, "gpu": gpu, "reason": "no GPU memory requirement"}
        if not gpu["available"]:
            return {"eligible": False, "minimum_gpu_memory_mib": minimum_gpu_memory_mib, "gpu": gpu, "reason": "GPU telemetry is unavailable"}
        if gpu["memory_free_mib"] < minimum_gpu_memory_mib:
            return {"eligible": False, "minimum_gpu_memory_mib": minimum_gpu_memory_mib, "gpu": gpu, "reason": "insufficient free GPU memory"}
        return {"eligible": True, "minimum_gpu_memory_mib": minimum_gpu_memory_mib, "gpu": gpu, "reason": "GPU memory requirement met"}

    @staticmethod
    def _clean_worker_id(worker_id: str) -> str:
        value = str(worker_id or "").strip()
        if not value or len(value) > 120:
            raise WorkflowError("worker_id is required and must be <= 120 characters")
        if any(char not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-.:" for char in value):
            raise WorkflowError("worker_id contains unsupported characters")
        return value

    def register_worker(
        self,
        worker_id: str,
        *,
        capabilities: list[str] | None = None,
        resources: dict[str, Any] | None = None,
        concurrency: int = 1,
        tenant_id: str | None = None,
    ) -> dict[str, Any]:
        with self._state_lock:
            return self._register_worker_locked(worker_id, capabilities=capabilities, resources=resources, concurrency=concurrency, tenant_id=tenant_id)

    def _register_worker_locked(
        self,
        worker_id: str,
        *,
        capabilities: list[str] | None = None,
        resources: dict[str, Any] | None = None,
        concurrency: int = 1,
        tenant_id: str | None = None,
    ) -> dict[str, Any]:
        clean_id = self._clean_worker_id(worker_id)
        if concurrency < 1 or concurrency > 64:
            raise WorkflowError("worker concurrency must be between 1 and 64")
        now = datetime.now(timezone.utc).isoformat()
        existing = self.workers.get(clean_id) or {}
        if existing and tenant_id is not None and existing.get("tenant_id") != tenant_id:
            raise WorkflowError("worker is not registered for this tenant")
        if resources is None:
            resources = {}
        if not isinstance(resources, dict):
            raise WorkflowError("worker resources must be an object")
        try:
            serialized_resources = json.dumps(resources, ensure_ascii=True)
        except (TypeError, ValueError) as exc:
            raise WorkflowError("worker resources must be JSON serializable") from exc
        if len(serialized_resources) > 32_000:
            raise WorkflowError("worker resources exceed 32 KiB")
        self.workers[clean_id] = {
            "worker_id": clean_id,
            "tenant_id": existing.get("tenant_id", tenant_id),
            "status": "ONLINE",
            "capabilities": sorted({str(item).strip() for item in (capabilities or []) if str(item).strip()}),
            "resources": {
                str(key): value for key, value in resources.items() if str(key).strip()
            },
            "concurrency": concurrency,
            "registered_at": existing.get("registered_at") or now,
            "last_heartbeat_at": now,
            "completed_jobs": int(existing.get("completed_jobs") or 0),
            "failed_jobs": int(existing.get("failed_jobs") or 0),
        }
        self._persist()
        return next(
            item for item in self.worker_status()["workers"]
            if item["worker_id"] == clean_id
        )

    def worker_heartbeat(
        self,
        worker_id: str,
        *,
        job_ids: list[str] | None = None,
        resources: dict[str, Any] | None = None,
        tenant_id: str | None = None,
    ) -> dict[str, Any]:
        with self._state_lock:
            return self._worker_heartbeat_locked(
                worker_id, job_ids=job_ids, resources=resources, tenant_id=tenant_id
            )

    def _worker_heartbeat_locked(
        self,
        worker_id: str,
        *,
        job_ids: list[str] | None = None,
        resources: dict[str, Any] | None = None,
        tenant_id: str | None = None,
    ) -> dict[str, Any]:
        clean_id = self._clean_worker_id(worker_id)
        worker = self.workers.get(clean_id)
        if worker is None:
            raise WorkflowError(f"worker is not registered: {clean_id}")
        if tenant_id is not None and worker.get("tenant_id") != tenant_id:
            raise WorkflowError("worker is not registered for this tenant")
        now = datetime.now(timezone.utc)
        refreshed: list[str] = []
        rejected: list[str] = []
        requested = job_ids or [
            job.job_id
            for job in self.jobs.all()
            if job.worker_id == clean_id
        ]
        for job_id in requested:
            try:
                job = self.jobs.get(job_id)
            except KeyError:
                rejected.append(job_id)
                continue
            if job.worker_id != clean_id or job.status not in {
                JobStatus.ADMITTED,
                JobStatus.RUNNING,
            }:
                rejected.append(job_id)
                continue
            job.last_heartbeat_at = now
            job.lease_expires_at = now + timedelta(
                seconds=self.job_lease_policy.stale_after_seconds
            )
            refreshed.append(job_id)
        worker["status"] = "ONLINE"
        worker["last_heartbeat_at"] = now.isoformat()
        if resources is not None:
            if not isinstance(resources, dict):
                raise WorkflowError("worker resources must be an object")
            try:
                serialized_resources = json.dumps(resources, ensure_ascii=True)
            except (TypeError, ValueError) as exc:
                raise WorkflowError("worker resources must be JSON serializable") from exc
            if len(serialized_resources) > 32_000:
                raise WorkflowError("worker resources exceed 32 KiB")
            worker["resources"] = {
                str(key): value for key, value in resources.items() if str(key).strip()
            }
        self._persist()
        return {
            "worker": next(
                item for item in self.worker_status()["workers"]
                if item["worker_id"] == clean_id
            ),
            "refreshed_job_ids": refreshed,
            "rejected_job_ids": rejected,
        }

    def claim_worker_jobs(
        self,
        worker_id: str,
        *,
        limit: int = 1,
        project_id: str | None = None,
        minimum_gpu_memory_mib: int = 0,
        provider_name: str | None = None,
        tenant_id: str | None = None,
    ) -> dict[str, Any]:
        with self._state_lock:
            return self._claim_worker_jobs_locked(
                worker_id,
                limit=limit,
                project_id=project_id,
                minimum_gpu_memory_mib=minimum_gpu_memory_mib,
                provider_name=provider_name,
                tenant_id=tenant_id,
            )

    def _claim_worker_jobs_locked(
        self,
        worker_id: str,
        *,
        limit: int = 1,
        project_id: str | None = None,
        minimum_gpu_memory_mib: int = 0,
        provider_name: str | None = None,
        tenant_id: str | None = None,
    ) -> dict[str, Any]:
        clean_id = self._clean_worker_id(worker_id)
        worker = self.workers.get(clean_id)
        if worker is None:
            raise WorkflowError(f"worker is not registered: {clean_id}")
        if tenant_id is not None and worker.get("tenant_id") != tenant_id:
            raise WorkflowError("worker is not registered for this tenant")
        tenant_id = tenant_id or worker.get("tenant_id")
        if limit < 1 or limit > 64:
            raise WorkflowError("worker claim limit must be between 1 and 64")
        if minimum_gpu_memory_mib < 0 or minimum_gpu_memory_mib > 1_048_576:
            raise WorkflowError("minimum_gpu_memory_mib must be between 0 and 1048576")
        clean_provider_name = str(provider_name or "").strip() or None
        if clean_provider_name and len(clean_provider_name) > 120:
            raise WorkflowError("provider_name must be <= 120 characters")
        admission = self._worker_gpu_admission(worker, minimum_gpu_memory_mib)
        if not admission["eligible"]:
            worker["status"] = "ONLINE"
            worker["last_heartbeat_at"] = datetime.now(timezone.utc).isoformat()
            self._persist()
            return {
                "worker_id": clean_id,
                "claimed_count": 0,
                "jobs": [],
                "queue_warnings": [],
                "admission": admission,
                "provider_name": clean_provider_name,
                "worker": next(item for item in self.worker_status()["workers"] if item["worker_id"] == clean_id),
            }
        eligible_projects = {
            item.brief.project_id for item in self.projects.values()
            if item.archived_at is None
            and (project_id is None or item.brief.project_id == project_id)
            and (tenant_id is None or item.brief.tenant_id == tenant_id)
        }
        for candidate_project in eligible_projects:
            self._recover_stale_jobs(project_id=candidate_project, actor=clean_id)
            self._promote_due_retries(candidate_project, actor=clean_id)
        active_count = sum(
            1
            for job in self.jobs.all()
            if job.worker_id == clean_id
            and job.status in {JobStatus.ADMITTED, JobStatus.RUNNING}
        )
        capacity = max(int(worker.get("concurrency") or 1) - active_count, 0)
        claim_limit = min(limit, capacity)
        now = datetime.now(timezone.utc)
        candidates = [
            job
            for job in self.jobs.all()
            if job.status in {JobStatus.VALIDATED, JobStatus.QUEUED}
            and job.spec.project_id in eligible_projects
            and (project_id is None or job.spec.project_id == project_id)
            and (
                tenant_id is None
                or (
                    self.projects.get(job.spec.project_id)
                    and self.projects[job.spec.project_id].brief.tenant_id == tenant_id
                )
            )
            and (
                not worker.get("capabilities")
                or job.spec.provider_constraints.capability.value
                in set(worker.get("capabilities") or [])
            )
            and (
                clean_provider_name is None
                or self._routed_provider_for_job(
                    self.projects[job.spec.project_id], job.job_id
                )
                == clean_provider_name
            )
        ]
        candidates.sort(
            key=lambda job: job.events[-1].occurred_at if job.events else now
        )
        if os.getenv("MEDIAFORGE_QUEUE_BACKEND", "sqlite").strip().lower() == "redis":
            try:
                ordered_ids = self.enterprise.redis_queue.ordered_pending([job.job_id for job in candidates])
            except EnterpriseConfigurationError as exc:
                raise WorkflowError(str(exc)) from exc
            order = {job_id: index for index, job_id in enumerate(ordered_ids)}
            candidates.sort(key=lambda job: order.get(job.job_id, len(order)))
        claimed = []
        for job in candidates[:claim_limit]:
            if job.status == JobStatus.VALIDATED:
                self.jobs.transition(job.job_id, JobStatus.QUEUED, reason="worker claim")
            self.jobs.transition(job.job_id, JobStatus.ADMITTED, reason=f"claimed by {clean_id}")
            job.worker_id = clean_id
            job.last_heartbeat_at = now
            job.lease_expires_at = now + timedelta(
                seconds=self.job_lease_policy.stale_after_seconds
            )
            project = self.projects.get(job.spec.project_id)
            if project:
                self._record_event(
                    project,
                    action="job.claimed",
                    actor=clean_id,
                    message=f"{job.spec.shot_id} was claimed by worker {clean_id}.",
                    shot_id=job.spec.shot_id,
                    details={"job_id": job.job_id, "worker_id": clean_id},
                )
            view = self._job_view(job)
            routed_provider = self._routed_provider_for_job(project, job.job_id)
            if routed_provider:
                view["routed_provider"] = routed_provider
            claimed.append(view)
        worker["status"] = "ONLINE"
        worker["last_heartbeat_at"] = now.isoformat()
        self._persist()
        queue_warnings = []
        if os.getenv("MEDIAFORGE_QUEUE_BACKEND", "sqlite").strip().lower() == "redis":
            for item in claimed:
                try:
                    self.enterprise.redis_queue.acknowledge(item["job_id"])
                except EnterpriseConfigurationError as exc:
                    # A committed lease is still valid if its Redis index cleanup fails.
                    queue_warnings.append(str(exc))
        return {
            "worker_id": clean_id,
            "claimed_count": len(claimed),
            "jobs": claimed,
            "queue_warnings": queue_warnings,
            "admission": admission,
            "provider_name": clean_provider_name,
            "worker": next(
                item for item in self.worker_status()["workers"]
                if item["worker_id"] == clean_id
            ),
        }

    def _assert_worker_lease(self, job: JobRecord, worker_id: str) -> None:
        clean_id = self._clean_worker_id(worker_id)
        if job.worker_id != clean_id:
            raise WorkflowError("job is leased to a different worker")
        if job.lease_expires_at and job.lease_expires_at <= datetime.now(timezone.utc):
            raise WorkflowError("worker lease has expired; heartbeat before processing")

    def _release_job_lease(self, job: JobRecord) -> None:
        if job.worker_id and job.status in {
            JobStatus.SUCCEEDED,
            JobStatus.FAILED,
            JobStatus.QUALITY_REJECTED,
            JobStatus.CANCELED,
            JobStatus.RETRY_WAIT,
        }:
            worker = self.workers.get(job.worker_id)
            if worker:
                if job.status == JobStatus.SUCCEEDED:
                    worker["completed_jobs"] = int(worker.get("completed_jobs") or 0) + 1
                elif job.status in {JobStatus.FAILED, JobStatus.QUALITY_REJECTED}:
                    worker["failed_jobs"] = int(worker.get("failed_jobs") or 0) + 1
            job.worker_id = None
            job.lease_expires_at = None
            job.last_heartbeat_at = None

    def license_registry_view(self) -> dict[str, Any]:
        view = self.license_registry.view()
        view["management"] = copy.deepcopy(self.license_registry_metadata)
        return view

    def validate_license_registry(
        self,
        records: list[dict[str, Any]],
        *,
        source: str = "studio-validation",
    ) -> dict[str, Any]:
        registry = LicenseRegistry.from_payload({"records": records}, source=source)
        view = registry.view()
        return {
            "valid": True,
            "schema_version": registry.schema_version,
            "source": source,
            "summary": view["summary"],
            "records": view["records"],
        }

    def import_license_registry(
        self,
        records: list[dict[str, Any]],
        *,
        source: str = "studio-import",
        actor: str = "studio-governance",
    ) -> dict[str, Any]:
        registry = LicenseRegistry.from_payload({"records": records}, source=source)
        return self._install_license_registry(registry, actor=actor)

    def _install_license_registry(
        self,
        registry: LicenseRegistry,
        *,
        actor: str,
        sync_metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        change_id = f"registry_{uuid4().hex[:16]}"
        self.license_registry = registry
        self.license_registry_metadata = {
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "updated_by": actor,
            "change_id": change_id,
        }
        if sync_metadata is not None:
            self.license_registry_metadata["sync"] = copy.deepcopy(sync_metadata)
        path = self.output_root / "studio" / "license-registry.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(self.license_registry_view(), ensure_ascii=True, indent=2),
            encoding="utf-8",
        )
        self._persist()
        view = self.license_registry_view()
        view["registry_path"] = str(path)
        return view

    def sync_license_registry(
        self,
        *,
        url: str | None = None,
        timeout_seconds: float = 10.0,
        actor: str = "studio-governance",
    ) -> dict[str, Any]:
        configured_url = os.getenv("MEDIAFORGE_LICENSE_REGISTRY_SYNC_URL", "").strip()
        target_url = (url or configured_url).strip()
        if not target_url:
            raise RegistrySyncError(
                "configure MEDIAFORGE_LICENSE_REGISTRY_SYNC_URL before syncing the license registry",
                status_code=422,
            )
        if url and configured_url and target_url != configured_url:
            raise RegistrySyncError(
                "sync URL must match MEDIAFORGE_LICENSE_REGISTRY_SYNC_URL",
                status_code=422,
            )
        if url and not configured_url and not os.getenv(
            "MEDIAFORGE_LICENSE_REGISTRY_SYNC_ALLOWED_HOSTS",
            "",
        ).strip():
            raise RegistrySyncError(
                "explicit sync URLs require MEDIAFORGE_LICENSE_REGISTRY_SYNC_ALLOWED_HOSTS",
                status_code=422,
            )
        target_url = validate_sync_url(target_url)

        previous_sync = dict(self.license_registry_metadata.get("sync") or {})
        attempted_at = datetime.now(timezone.utc).isoformat()
        try:
            fetched = fetch_registry(
                target_url,
                timeout_seconds=timeout_seconds,
                etag=previous_sync.get("etag")
                if previous_sync.get("url") == target_url
                else None,
                last_modified=previous_sync.get("last_modified")
                if previous_sync.get("url") == target_url
                else None,
                token=os.getenv("MEDIAFORGE_LICENSE_REGISTRY_SYNC_TOKEN"),
            )
            if fetched.not_modified:
                sync_metadata = {
                    **previous_sync,
                    "url": target_url,
                    "last_attempt_at": attempted_at,
                    "last_status": "NOT_MODIFIED",
                    "last_error": None,
                }
                self.license_registry_metadata["sync"] = sync_metadata
                self._persist()
                view = self.license_registry_view()
                view["sync_result"] = {
                    "synced": False,
                    "not_modified": True,
                    "status_code": fetched.status_code,
                }
                return view
            if fetched.payload is None:
                raise RegistrySyncError(
                    "license registry sync returned an empty payload",
                    status_code=502,
                )
            remote_records = fetched.payload.get("records")
            if not isinstance(remote_records, list) or not 1 <= len(remote_records) <= 500:
                raise RegistrySyncError(
                    "license registry sync payload must contain between 1 and 500 records",
                    status_code=502,
                )
            try:
                registry = LicenseRegistry.from_payload(
                    fetched.payload,
                    source=target_url,
                )
            except ValueError as exc:
                raise RegistrySyncError(
                    f"license registry sync payload is invalid: {exc}",
                    status_code=502,
                ) from exc
            sync_metadata = {
                "url": target_url,
                "last_attempt_at": attempted_at,
                "last_success_at": attempted_at,
                "last_status": "SYNCED",
                "last_error": None,
                "etag": fetched.etag,
                "last_modified": fetched.last_modified,
                "content_sha256": fetched.content_sha256,
                "content_length": fetched.content_length,
                "record_count": len(registry.records),
            }
            view = self._install_license_registry(
                registry,
                actor=actor,
                sync_metadata=sync_metadata,
            )
            view["sync_result"] = {
                "synced": True,
                "not_modified": False,
                "status_code": fetched.status_code,
            }
            return view
        except RegistrySyncError as exc:
            self.license_registry_metadata["sync"] = {
                **previous_sync,
                "url": target_url,
                "last_attempt_at": attempted_at,
                "last_status": "FAILED",
                "last_error": str(exc),
            }
            self._persist()
            raise

    def export_license_registry(self) -> Path:
        path = self.output_root / "studio" / "license-registry.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(self.license_registry_view(), ensure_ascii=True, indent=2),
            encoding="utf-8",
        )
        return path

    def create_project(
        self,
        brief: CreativeBrief,
        *,
        owner_subject: str = "studio-user",
    ) -> dict[str, Any]:
        if brief.project_id in self.projects:
            raise WorkflowError(f"project already exists: {brief.project_id}")
        self._enforce_project_quota(
            brief.tenant_id,
            additional_budget=float(brief.budget),
        )
        policy_report = self.policy.assess_brief(brief).as_dict()
        if not policy_report["passed"]:
            raise PolicyViolation(
                "content policy blocked project brief",
                policy_report,
            )
        project = ProjectRuntime(
            brief=brief,
            prompt_versions=default_prompt_versions(),
            members=[
                {
                    "subject": owner_subject.strip() or "studio-user",
                    "role": "owner",
                    "added_at": datetime.now(timezone.utc).isoformat(),
                }
            ],
        )
        project.policy_reports.append(policy_report)
        self.projects[brief.project_id] = project
        self._record_event(
            project,
            action="project.created",
            actor=owner_subject.strip() or "studio-user",
            message=f"Project {brief.project_id} created.",
            details={"budget": brief.budget, "duration_seconds": brief.duration_seconds},
        )
        self._record_event(
            project,
            action="llmops.prompts_initialized",
            actor="system",
            message="Project prompt registry initialized with versioned defaults.",
            details={"prompt_count": len(project.prompt_versions)},
        )
        self._record_event(
            project,
            action="policy.brief_checked",
            actor="policy-engine",
            message="Project brief passed the local policy gate.",
            details=policy_report,
        )
        self._persist()
        return self.project_view(brief.project_id)

    def tenant_quota(self, tenant_id: str | None) -> dict[str, Any]:
        clean_tenant = str(tenant_id or "default")
        quota = self.quota_policy.for_tenant(clean_tenant)
        projects = [
            project
            for project in self.projects.values()
            if project.brief.tenant_id == clean_tenant and project.archived_at is None
        ]
        jobs = [
            job
            for job in self.jobs.all()
            if any(
                project.brief.project_id == job.spec.project_id
                for project in self.projects.values()
                if project.brief.tenant_id == clean_tenant
            )
        ]
        allocated_budget = sum(float(project.brief.budget) for project in projects)
        spent = sum(self._cost_report(project)["spent"] for project in projects)
        usage = {
            "project_count": len(projects),
            "job_count": len(jobs),
            "allocated_budget": round(allocated_budget, 4),
            "spent": round(spent, 4),
        }
        remaining = {
            "projects": (
                None
                if quota.max_projects is None
                else max(quota.max_projects - usage["project_count"], 0)
            ),
            "jobs": (
                None
                if quota.max_jobs is None
                else max(quota.max_jobs - usage["job_count"], 0)
            ),
            "budget": (
                None
                if quota.max_budget is None
                else round(max(quota.max_budget - usage["allocated_budget"], 0.0), 4)
            ),
        }
        return {
            "schema_version": "mediaforge-tenant-usage-v1",
            "tenant_id": clean_tenant,
            "quota": quota.as_dict(),
            "usage": usage,
            "remaining": remaining,
            "configured": self.quota_policy.configured,
        }

    def quota_status(self) -> dict[str, Any]:
        return self.quota_policy.status_view()

    def update_project_brief(
        self,
        project_id: str,
        *,
        changes: dict[str, Any],
        actor: str = "studio-editor",
    ) -> dict[str, Any]:
        project = self._project(project_id)
        self._ensure_active(project)
        clean_changes = {key: value for key, value in changes.items() if key != "actor"}
        if not clean_changes:
            return self.project_view(project_id)
        allowed = {
            "title",
            "premise",
            "genre",
            "style",
            "duration_seconds",
            "budget",
            "characters",
        }
        unknown = set(clean_changes) - allowed
        if unknown:
            raise WorkflowError(f"unsupported project fields: {', '.join(sorted(unknown))}")
        if (set(clean_changes) - {"title", "budget"}) and project.shots:
            raise WorkflowError(
                "planned project content must be revised through a branch or shot revision"
            )
        nullable_fields = {
            key for key, value in clean_changes.items() if value is None
        }
        if nullable_fields:
            raise WorkflowError(
                f"project fields cannot be null: {', '.join(sorted(nullable_fields))}"
            )
        candidate = project.brief.model_copy(update=clean_changes)
        if candidate.budget < self._cost_report(project)["spent"]:
            raise WorkflowError("project budget cannot be lower than already spent cost")
        policy_report = self.policy.assess_brief(candidate).as_dict()
        if not policy_report["passed"]:
            raise PolicyViolation("content policy blocked project brief", policy_report)
        if candidate.budget > project.brief.budget:
            report = self.tenant_quota(candidate.tenant_id)
            max_budget = report["quota"].get("max_budget")
            requested_budget = round(
                report["usage"]["allocated_budget"] - project.brief.budget + candidate.budget,
                4,
            )
            if max_budget is not None and requested_budget > max_budget:
                raise QuotaViolation(
                    "tenant allocated budget quota exceeded",
                    report={
                        **report,
                        "resource": "budget",
                        "requested": {"budget": requested_budget},
                    },
                )
        self._invalidate_workflow_from(
            project,
            "script",
            reason="project_brief_updated",
            actor=actor,
        )
        project.brief = candidate
        project.policy_reports.append(policy_report)
        self._record_event(
            project,
            action="project.brief_updated",
            actor=actor,
            message=f"Project {project_id} brief updated.",
            details={"fields": sorted(clean_changes)},
        )
        self._persist()
        return self.project_view(project_id)

    @staticmethod
    def _ordered_narrative_events(
        project: ProjectRuntime,
        *,
        approved_only: bool = False,
    ) -> list[NarrativeEvent]:
        events = project.narrative_events
        if approved_only:
            events = [
                event
                for event in events
                if event.review_status == ReviewStatus.APPROVED
            ]
        return sorted(
            events,
            key=lambda event: (
                event.chapter_number,
                event.sequence,
                event.event_id,
            ),
        )

    @staticmethod
    def _narrative_event_view(event: NarrativeEvent) -> dict[str, Any]:
        return event.model_dump(mode="json")

    def narrative_events_view(self, project_id: str) -> dict[str, Any]:
        project = self._project(project_id)
        events = self._ordered_narrative_events(project)
        return {
            "schema_version": "mediaforge-narrative-events-v1",
            "project_id": project_id,
            "count": len(events),
            "approved_count": sum(
                event.review_status == ReviewStatus.APPROVED
                for event in events
            ),
            "events": [self._narrative_event_view(event) for event in events],
        }

    def approved_narrative_events(self, project_id: str) -> list[NarrativeEvent]:
        return self._ordered_narrative_events(
            self._project(project_id),
            approved_only=True,
        )

    @staticmethod
    def _ordered_source_chapters(
        project: ProjectRuntime,
    ) -> list[NarrativeSourceChapter]:
        return sorted(
            project.source_chapters,
            key=lambda chapter: (chapter.chapter_number, chapter.chapter_id),
        )

    @staticmethod
    def _source_chapter_view(chapter: NarrativeSourceChapter) -> dict[str, Any]:
        return chapter.model_dump(mode="json")

    @staticmethod
    def _source_document_view(document: NarrativeSourceDocument) -> dict[str, Any]:
        return document.model_dump(mode="json")

    @staticmethod
    def _source_document(
        project: ProjectRuntime,
        document_id: str,
    ) -> NarrativeSourceDocument:
        document = next(
            (
                item
                for item in project.source_documents
                if item.document_id == document_id
            ),
            None,
        )
        if document is None:
            raise WorkflowError(f"source document not found: {document_id}")
        return document

    @staticmethod
    def _ordered_narrative_event_candidates(
        project: ProjectRuntime,
    ) -> list[NarrativeEventCandidate]:
        chapter_numbers = {
            chapter.chapter_id: chapter.chapter_number
            for chapter in project.source_chapters
        }
        return sorted(
            project.narrative_event_candidates,
            key=lambda candidate: (
                chapter_numbers.get(candidate.source_chapter_id, 100_001),
                candidate.sequence,
                candidate.candidate_id,
            ),
        )

    @staticmethod
    def _narrative_event_candidate_view(
        candidate: NarrativeEventCandidate,
    ) -> dict[str, Any]:
        return candidate.model_dump(mode="json")

    def source_chapters_view(self, project_id: str) -> dict[str, Any]:
        project = self._project(project_id)
        chapters = self._ordered_source_chapters(project)
        candidates = self._ordered_narrative_event_candidates(project)
        return {
            "schema_version": "mediaforge-narrative-source-v1",
            "project_id": project_id,
            "chapter_count": len(chapters),
            "document_count": len(project.source_documents),
            "candidate_count": len(candidates),
            "pending_candidate_count": sum(
                candidate.status == NarrativeCandidateStatus.PENDING
                for candidate in candidates
            ),
            "chapters": [self._source_chapter_view(chapter) for chapter in chapters],
            "documents": [
                self._source_document_view(document)
                for document in project.source_documents
            ],
            "candidates": [
                self._narrative_event_candidate_view(candidate)
                for candidate in candidates
            ],
        }

    @staticmethod
    def _source_chapter_sha256(chapter: NarrativeSourceChapterInput) -> str:
        return hashlib.sha256(chapter.content.encode("utf-8")).hexdigest()

    @staticmethod
    def _source_chapter(
        project: ProjectRuntime,
        chapter_id: str,
    ) -> NarrativeSourceChapter:
        chapter = next(
            (
                item
                for item in project.source_chapters
                if item.chapter_id == chapter_id
            ),
            None,
        )
        if chapter is None:
            raise WorkflowError(f"source chapter not found: {chapter_id}")
        return chapter

    @staticmethod
    def _narrative_event_source_chapter_sha(
        project: ProjectRuntime,
        event: NarrativeEventInput,
    ) -> str | None:
        if not event.source_chapter_id:
            return None
        return MediaForgeService._source_chapter(
            project,
            event.source_chapter_id,
        ).content_sha256

    def _assert_narrative_event_source_current(
        self,
        project: ProjectRuntime,
        event: NarrativeEvent,
    ) -> None:
        if not event.source_chapter_id:
            return
        chapter = self._source_chapter(project, event.source_chapter_id)
        if event.source_chapter_content_sha256 != chapter.content_sha256:
            raise WorkflowError(
                "narrative event source is stale; re-extract and adopt a current candidate"
            )

    @staticmethod
    def _ordered_adaptation_scenes(
        project: ProjectRuntime,
        *,
        approved_only: bool = False,
    ) -> list[AdaptationScene]:
        scenes = project.adaptation_scenes
        if approved_only:
            scenes = [
                scene
                for scene in scenes
                if scene.review_status == ReviewStatus.APPROVED
            ]
        return sorted(scenes, key=lambda scene: (scene.sequence, scene.scene_id))

    @staticmethod
    def _adaptation_scene_view(scene: AdaptationScene) -> dict[str, Any]:
        return scene.model_dump(mode="json")

    def adaptation_scenes_view(self, project_id: str) -> dict[str, Any]:
        project = self._project(project_id)
        scenes = self._ordered_adaptation_scenes(project)
        return {
            "schema_version": "mediaforge-adaptation-scenes-v1",
            "project_id": project_id,
            "count": len(scenes),
            "approved_count": sum(
                scene.review_status == ReviewStatus.APPROVED
                for scene in scenes
            ),
            "scenes": [self._adaptation_scene_view(scene) for scene in scenes],
        }

    def _scene_source_events(
        self,
        project: ProjectRuntime,
        source_event_ids: list[str],
        *,
        require_approved: bool,
    ) -> list[NarrativeEvent]:
        events_by_id = {event.event_id: event for event in project.narrative_events}
        resolved: list[NarrativeEvent] = []
        for event_id in source_event_ids:
            event = events_by_id.get(event_id)
            if event is None:
                raise WorkflowError(f"source narrative event not found: {event_id}")
            if require_approved and event.review_status != ReviewStatus.APPROVED:
                raise WorkflowError(
                    "adaptation scenes require approved source narrative events"
                )
            self._assert_narrative_event_source_current(project, event)
            resolved.append(event)
        return resolved

    def _adaptation_scene_source_sha256(
        self,
        project: ProjectRuntime,
        source_event_ids: list[str],
    ) -> str:
        events = self._scene_source_events(
            project,
            source_event_ids,
            require_approved=True,
        )
        records = [
            {
                "event_id": event.event_id,
                "revision": event.revision,
                "source_sha256": event.source_sha256,
                "source_chapter_content_sha256": event.source_chapter_content_sha256,
                "review_status": event.review_status,
            }
            for event in events
        ]
        return hashlib.sha256(
            json.dumps(records, ensure_ascii=False, sort_keys=True).encode("utf-8")
        ).hexdigest()

    def _assert_adaptation_scene_sources_current(
        self,
        project: ProjectRuntime,
        scene: AdaptationScene,
    ) -> None:
        current_sha256 = self._adaptation_scene_source_sha256(
            project,
            scene.source_event_ids,
        )
        if scene.source_events_sha256 != current_sha256:
            raise WorkflowError(
                "adaptation scene sources are stale; derive or revise the scene"
            )

    def approved_adaptation_scenes(self, project_id: str) -> list[AdaptationScene]:
        project = self._project(project_id)
        scenes = self._ordered_adaptation_scenes(project, approved_only=True)
        for scene in scenes:
            self._assert_adaptation_scene_sources_current(project, scene)
        return scenes

    def _adaptation_scene_context(self, project: ProjectRuntime) -> dict[str, Any]:
        scenes = self.approved_adaptation_scenes(project.brief.project_id)
        records = [
            {
                "scene_id": scene.scene_id,
                "revision": scene.revision,
                "sequence": scene.sequence,
                "heading": scene.heading,
                "synopsis": scene.synopsis,
                "beats": list(scene.beats),
                "dialogue_draft": scene.dialogue_draft,
                "characters": list(scene.characters),
                "mood": scene.mood,
                "estimated_duration_seconds": scene.estimated_duration_seconds,
                "source_event_ids": list(scene.source_event_ids),
                "source_events_sha256": scene.source_events_sha256,
            }
            for scene in scenes
        ]
        return {
            "schema_version": "mediaforge-adaptation-scene-context-v1",
            "approved_scene_count": len(records),
            "scene_ids": [record["scene_id"] for record in records],
            "scenes": records,
            "context_sha256": hashlib.sha256(
                json.dumps(records, ensure_ascii=False, sort_keys=True).encode("utf-8")
            ).hexdigest(),
        }

    def _assert_adaptation_scenes_mutable(self, project: ProjectRuntime) -> None:
        self._assert_narrative_events_mutable(project)

    @staticmethod
    def _return_adaptation_scenes_for_event_ids(
        project: ProjectRuntime,
        event_ids: list[str],
        *,
        now: datetime,
    ) -> list[str]:
        """Return dependent scenes when an upstream event no longer matches them."""
        affected = set(event_ids)
        returned_scene_ids: list[str] = []
        if not affected:
            return returned_scene_ids
        for index, scene in enumerate(project.adaptation_scenes):
            if (
                affected.intersection(scene.source_event_ids)
                and scene.review_status != ReviewStatus.CHANGES_REQUESTED
            ):
                project.adaptation_scenes[index] = scene.model_copy(
                    update={
                        "review_status": ReviewStatus.CHANGES_REQUESTED,
                        "revision": scene.revision + 1,
                        "updated_at": now,
                    }
                )
                returned_scene_ids.append(scene.scene_id)
        return returned_scene_ids

    def derive_adaptation_scenes(
        self,
        project_id: str,
        *,
        actor: str = "script-editor",
    ) -> dict[str, Any]:
        with self.planning.mutation_guard(project_id):
            project = self._project(project_id)
            self._assert_adaptation_scenes_mutable(project)
            events = self.approved_narrative_events(project_id)
            if not events:
                raise WorkflowError(
                    "approve at least one narrative event before deriving adaptation scenes"
                )
            now = datetime.now(timezone.utc)
            stale_scene_ids: list[str] = []
            for index, scene in enumerate(project.adaptation_scenes):
                if scene.review_status == ReviewStatus.PENDING:
                    project.adaptation_scenes[index] = scene.model_copy(
                        update={
                            "review_status": ReviewStatus.CHANGES_REQUESTED,
                            "revision": scene.revision + 1,
                            "updated_at": now,
                        }
                    )
                    stale_scene_ids.append(scene.scene_id)
            scenes: list[AdaptationScene] = []
            for sequence, event in enumerate(events, start=1):
                source_event_ids = [event.event_id]
                scenes.append(
                    AdaptationScene(
                        scene_id=f"scene_{uuid4().hex[:16]}",
                        sequence=sequence,
                        heading=f"{event.scene} · {event.title}"[:240],
                        synopsis=event.summary,
                        beats=[event.summary],
                        dialogue_draft="",
                        characters=list(event.characters),
                        mood=(event.emotions[0] if event.emotions else ""),
                        estimated_duration_seconds=event.estimated_duration_seconds,
                        source_event_ids=source_event_ids,
                        source_events_sha256=self._adaptation_scene_source_sha256(
                            project,
                            source_event_ids,
                        ),
                        derived_from="approved-narrative-events-v1",
                        created_at=now,
                        updated_at=now,
                    )
                )
            project.adaptation_scenes.extend(scenes)
            invalidated = self._invalidate_plan_for_narrative_change(project)
            self._record_event(
                project,
                action="adaptation_scene.derived",
                actor=actor,
                message=f"Derived {len(scenes)} adaptation scenes from approved narrative events.",
                details={
                    "scene_ids": [scene.scene_id for scene in scenes],
                    "source_event_ids": [event.event_id for event in events],
                    "returned_scene_ids": stale_scene_ids,
                    "plan_invalidated": invalidated,
                },
            )
            if invalidated:
                self._record_event(
                    project,
                    action="project.plan_invalidated",
                    actor=actor,
                    message="Existing plan invalidated by adaptation scene derivation.",
                    details={"reason": "adaptation_scene.derived"},
                )
            self._persist()
            return {
                "scenes": [self._adaptation_scene_view(scene) for scene in scenes],
                "plan_invalidated": invalidated,
            }

    def create_adaptation_scene(
        self,
        project_id: str,
        scene: AdaptationSceneInput,
        *,
        actor: str = "script-editor",
    ) -> dict[str, Any]:
        with self.planning.mutation_guard(project_id):
            project = self._project(project_id)
            self._assert_adaptation_scenes_mutable(project)
            scene_input = AdaptationSceneInput.model_validate(
                {name: getattr(scene, name) for name in AdaptationSceneInput.model_fields}
            )
            self._validate_narrative_event_characters(project, scene_input)
            source_sha256 = self._adaptation_scene_source_sha256(
                project,
                scene_input.source_event_ids,
            )
            scene_id = scene_input.scene_id or f"scene_{uuid4().hex[:16]}"
            if any(item.scene_id == scene_id for item in project.adaptation_scenes):
                raise WorkflowError(f"adaptation scene already exists: {scene_id}")
            now = datetime.now(timezone.utc)
            record = AdaptationScene(
                **scene_input.model_dump(mode="json", exclude={"scene_id"}),
                scene_id=scene_id,
                source_events_sha256=source_sha256,
                created_at=now,
                updated_at=now,
            )
            project.adaptation_scenes.append(record)
            invalidated = self._invalidate_plan_for_narrative_change(project)
            self._record_event(
                project,
                action="adaptation_scene.created",
                actor=actor,
                message=f"Adaptation scene {record.scene_id} created.",
                details={
                    "scene_id": record.scene_id,
                    "revision": record.revision,
                    "source_event_ids": record.source_event_ids,
                    "source_events_sha256": record.source_events_sha256,
                    "plan_invalidated": invalidated,
                },
            )
            self._persist()
            return {
                "scene": self._adaptation_scene_view(record),
                "plan_invalidated": invalidated,
            }

    def update_adaptation_scene(
        self,
        project_id: str,
        scene_id: str,
        *,
        changes: dict[str, Any],
        expected_revision: int | None = None,
        actor: str = "script-editor",
    ) -> dict[str, Any]:
        with self.planning.mutation_guard(project_id):
            project = self._project(project_id)
            self._assert_adaptation_scenes_mutable(project)
            index = next(
                (
                    position
                    for position, item in enumerate(project.adaptation_scenes)
                    if item.scene_id == scene_id
                ),
                None,
            )
            if index is None:
                raise WorkflowError(f"adaptation scene not found: {scene_id}")
            current = project.adaptation_scenes[index]
            if expected_revision is not None and current.revision != expected_revision:
                raise WorkflowError(
                    "adaptation scene revision conflict; refresh before saving"
                )
            allowed = set(AdaptationSceneInput.model_fields) - {"scene_id"}
            clean_changes = {key: value for key, value in changes.items() if key in allowed}
            unknown = set(changes) - allowed
            if unknown:
                raise WorkflowError(
                    "unsupported adaptation scene fields: " + ", ".join(sorted(unknown))
                )
            if not clean_changes:
                return {"scene": self._adaptation_scene_view(current), "plan_invalidated": False}
            scene_input = AdaptationSceneInput.model_validate(
                {
                    **current.model_dump(
                        mode="json",
                        exclude={
                            "revision",
                            "review_status",
                            "source_events_sha256",
                            "derived_from",
                            "created_at",
                            "updated_at",
                        },
                    ),
                    **clean_changes,
                }
            )
            self._validate_narrative_event_characters(project, scene_input)
            now = datetime.now(timezone.utc)
            revised = AdaptationScene(
                **scene_input.model_dump(mode="json", exclude={"scene_id"}),
                scene_id=current.scene_id,
                revision=current.revision + 1,
                review_status=ReviewStatus.PENDING,
                source_events_sha256=self._adaptation_scene_source_sha256(
                    project,
                    scene_input.source_event_ids,
                ),
                derived_from=current.derived_from,
                created_at=current.created_at,
                updated_at=now,
            )
            project.adaptation_scenes[index] = revised
            invalidated = self._invalidate_plan_for_narrative_change(project)
            self._record_event(
                project,
                action="adaptation_scene.updated",
                actor=actor,
                message=f"Adaptation scene {scene_id} revised to version {revised.revision}.",
                details={
                    "scene_id": scene_id,
                    "revision": revised.revision,
                    "fields": sorted(clean_changes),
                    "source_event_ids": revised.source_event_ids,
                    "source_events_sha256": revised.source_events_sha256,
                    "plan_invalidated": invalidated,
                },
            )
            self._persist()
            return {
                "scene": self._adaptation_scene_view(revised),
                "plan_invalidated": invalidated,
            }

    def review_adaptation_scene(
        self,
        project_id: str,
        scene_id: str,
        *,
        status: ReviewStatus,
        comment: str = "",
        expected_revision: int | None = None,
        actor: str = "script-reviewer",
    ) -> dict[str, Any]:
        if status not in {ReviewStatus.APPROVED, ReviewStatus.CHANGES_REQUESTED}:
            raise WorkflowError("adaptation scene review must approve or request changes")
        with self.planning.mutation_guard(project_id):
            project = self._project(project_id)
            self._assert_adaptation_scenes_mutable(project)
            index = next(
                (
                    position
                    for position, item in enumerate(project.adaptation_scenes)
                    if item.scene_id == scene_id
                ),
                None,
            )
            if index is None:
                raise WorkflowError(f"adaptation scene not found: {scene_id}")
            current = project.adaptation_scenes[index]
            if expected_revision is not None and current.revision != expected_revision:
                raise WorkflowError(
                    "adaptation scene revision conflict; refresh before reviewing"
                )
            if status == ReviewStatus.APPROVED:
                self._assert_adaptation_scene_sources_current(project, current)
            if current.review_status == status:
                return {"scene": self._adaptation_scene_view(current), "plan_invalidated": False}
            revised = current.model_copy(
                update={
                    "review_status": status,
                    "updated_at": datetime.now(timezone.utc),
                }
            )
            project.adaptation_scenes[index] = revised
            invalidated = self._invalidate_plan_for_narrative_change(project)
            self._record_event(
                project,
                action="adaptation_scene.reviewed",
                actor=actor,
                message=f"Adaptation scene {scene_id} reviewed as {status}.",
                details={
                    "scene_id": scene_id,
                    "revision": revised.revision,
                    "status": status,
                    "comment": comment,
                    "plan_invalidated": invalidated,
                },
            )
            self._persist()
            return {
                "scene": self._adaptation_scene_view(revised),
                "plan_invalidated": invalidated,
            }

    def _assert_narrative_events_mutable(self, project: ProjectRuntime) -> None:
        self._ensure_active(project)
        if self.planning.has_active(project.brief.project_id):
            raise PlanningError("approve or cancel the active planning draft first")
        if (
            project.release
            or project.final_mp4
            or any(runtime.current_artifact for runtime in project.shots.values())
            or any(
                job.spec.project_id == project.brief.project_id
                for job in self.jobs.all()
            )
        ):
            raise WorkflowError(
                "narrative events are locked after media work begins; create a branch"
            )

    @staticmethod
    def _narrative_source_sha256(event: NarrativeEventInput) -> str:
        source = event.source_excerpt or json.dumps(
            event.model_dump(
                mode="json",
                exclude={"event_id", "source_excerpt"},
            ),
            ensure_ascii=False,
            sort_keys=True,
        )
        return hashlib.sha256(source.encode("utf-8")).hexdigest()

    @staticmethod
    def _validate_narrative_event_characters(
        project: ProjectRuntime,
        event: NarrativeEventInput,
    ) -> None:
        unknown = sorted(set(event.characters) - set(project.brief.characters))
        if unknown:
            raise WorkflowError(
                "narrative event characters must be declared in the project brief: "
                + ", ".join(unknown)
            )

    def _invalidate_plan_for_narrative_change(
        self,
        project: ProjectRuntime,
    ) -> bool:
        if not project.story_bible and not project.shots:
            return False
        self._invalidate_workflow_from(
            project,
            "script",
            reason="narrative_changed",
            actor="story-editor",
        )
        project.story_bible = None
        project.shots.clear()
        project.status = ProjectStatus.DRAFT
        project.subtitle_srt = None
        project.dialogue_timeline = None
        return True

    def _narrative_event_context(self, project: ProjectRuntime) -> dict[str, Any]:
        events = self._ordered_narrative_events(project, approved_only=True)
        records = [
            {
                "event_id": event.event_id,
                "revision": event.revision,
                "chapter_number": event.chapter_number,
                "sequence": event.sequence,
                "title": event.title,
                "scene": event.scene,
                "summary": event.summary,
                "characters": list(event.characters),
                "importance": event.importance,
                "emotions": list(event.emotions),
                "estimated_duration_seconds": event.estimated_duration_seconds,
                "source_chapter_id": event.source_chapter_id,
                "source_chapter_content_sha256": event.source_chapter_content_sha256,
                "source_locator": event.source_locator,
                "source_sha256": event.source_sha256,
            }
            for event in events
        ]
        return {
            "schema_version": "mediaforge-narrative-event-context-v1",
            "approved_event_count": len(records),
            "event_ids": [record["event_id"] for record in records],
            "events": records,
            "context_sha256": hashlib.sha256(
                json.dumps(records, ensure_ascii=False, sort_keys=True).encode("utf-8")
            ).hexdigest(),
        }

    @staticmethod
    def _local_narrative_event_proposals(
        project: ProjectRuntime,
        chapter: NarrativeSourceChapter,
    ) -> list[NarrativeEventCandidateProposal]:
        """Create extractive drafts without sending source text to a provider."""
        paragraphs = [
            re.sub(r"\s+", " ", paragraph).strip()
            for paragraph in re.split(r"\n\s*\n+", chapter.content.strip())
        ]
        paragraphs = [paragraph for paragraph in paragraphs if paragraph]
        if not paragraphs:
            paragraphs = [re.sub(r"\s+", " ", chapter.content).strip()]

        chunks: list[str] = []
        for paragraph in paragraphs:
            while len(paragraph) > 2_000:
                chunks.append(paragraph[:2_000])
                paragraph = paragraph[2_000:]
            if paragraph:
                chunks.append(paragraph)
        if not chunks:
            chunks = [chapter.content.strip()]

        proposals: list[NarrativeEventCandidateProposal] = []
        for sequence, chunk in enumerate(chunks[:12], start=1):
            characters = [
                character
                for character in project.brief.characters
                if character in chunk
            ]
            suffix = "" if len(chunks) == 1 else f" · 片段 {sequence}"
            proposals.append(
                NarrativeEventCandidateProposal(
                    title=f"{chapter.title}{suffix}"[:240],
                    scene=f"第 {chapter.chapter_number} 章",
                    summary=chunk[:2_000],
                    characters=characters,
                    estimated_duration_seconds=5,
                    source_locator=(
                        f"{chapter.source_name} / 第 {chapter.chapter_number} 章"
                        f" / 片段 {sequence}"
                    ),
                    source_excerpt=chunk[:8_000],
                )
            )
        return proposals

    def _extract_narrative_event_proposals(
        self,
        project: ProjectRuntime,
        chapter: NarrativeSourceChapter,
    ) -> tuple[list[NarrativeEventCandidateProposal], str, str | None, str | None]:
        extractor = getattr(self.story_planner, "extract_narrative_events", None)
        if chapter.allow_external_processing and callable(extractor):
            try:
                raw_proposals = extractor(project.brief, chapter)
                if not isinstance(raw_proposals, list) or not raw_proposals:
                    raise StoryPlannerError("LLM narrative extraction returned no candidates")
                proposals = []
                for sequence, proposal in enumerate(raw_proposals[:12], start=1):
                    candidate = NarrativeEventCandidateProposal.model_validate(proposal)
                    # Candidate provenance always points at the imported chapter, not model text.
                    proposals.append(
                        candidate.model_copy(
                            update={
                                "characters": [
                                    character
                                    for character in candidate.characters
                                    if character in project.brief.characters
                                ],
                                "source_locator": (
                                    f"{chapter.source_name} / 第 {chapter.chapter_number} 章"
                                    f" / 模型候选 {sequence}"
                                ),
                                "source_excerpt": None,
                            }
                        )
                    )
                return (
                    proposals,
                    "external-structured-v1",
                    str(getattr(self.story_planner, "model", "configured-model")),
                    None,
                )
            except (StoryPlannerError, ValueError, TypeError) as exc:
                fallback_reason = str(exc)[:500]
        else:
            fallback_reason = None
        return (
            self._local_narrative_event_proposals(project, chapter),
            "local-structural-v1",
            None,
            fallback_reason,
        )

    def create_source_chapter(
        self,
        project_id: str,
        chapter: NarrativeSourceChapterInput,
        *,
        actor: str = "story-editor",
    ) -> dict[str, Any]:
        with self.planning.mutation_guard(project_id):
            project = self._project(project_id)
            self._assert_narrative_events_mutable(project)
            source_input = NarrativeSourceChapterInput.model_validate(
                {
                    name: getattr(chapter, name)
                    for name in NarrativeSourceChapterInput.model_fields
                }
            )
            chapter_id = source_input.chapter_id or f"chapter_{uuid4().hex[:16]}"
            if any(item.chapter_id == chapter_id for item in project.source_chapters):
                raise WorkflowError(f"source chapter already exists: {chapter_id}")
            now = datetime.now(timezone.utc)
            record = NarrativeSourceChapter(
                **source_input.model_dump(mode="json", exclude={"chapter_id"}),
                chapter_id=chapter_id,
                revision=1,
                content_sha256=self._source_chapter_sha256(source_input),
                created_at=now,
                updated_at=now,
            )
            project.source_chapters.append(record)
            self._record_event(
                project,
                action="source_chapter.created",
                actor=actor,
                message=f"Source chapter {record.chapter_id} imported.",
                details={
                    "chapter_id": record.chapter_id,
                    "revision": record.revision,
                    "chapter_number": record.chapter_number,
                    "content_sha256": record.content_sha256,
                    "allow_external_processing": record.allow_external_processing,
                },
            )
            self._persist()
            return {"chapter": self._source_chapter_view(record)}

    def import_source_document(
        self,
        project_id: str,
        *,
        name: str,
        content_b64: str,
        source_name: str,
        rights_basis: str,
        allow_external_processing: bool = False,
        chapter_number_start: int = 1,
        actor: str = "story-editor",
    ) -> dict[str, Any]:
        with self.planning.mutation_guard(project_id):
            project = self._project(project_id)
            self._assert_narrative_events_mutable(project)
            clean_name = Path(name.strip()).name
            clean_source_name = source_name.strip()
            clean_rights_basis = rights_basis.strip()
            if not clean_name or not clean_source_name or not clean_rights_basis:
                raise WorkflowError(
                    "source document name, source name, and rights basis are required"
                )
            try:
                content = base64.b64decode(content_b64, validate=True)
            except (ValueError, TypeError) as exc:
                raise WorkflowError("content_b64 is not valid base64") from exc
            if len(content) > MAX_SOURCE_DOCUMENT_BYTES:
                raise WorkflowError("source document exceeds the 20 MB limit")
            try:
                document_format, parsed_chapters, extraction = parse_source_document(
                    clean_name,
                    content,
                    allow_external_ocr=allow_external_processing,
                )
            except SourceIngestError as exc:
                raise WorkflowError(str(exc)) from exc
            if chapter_number_start + len(parsed_chapters) - 1 > 100_000:
                raise WorkflowError("source document chapter numbering exceeds the supported range")

            source_dir = self.output_root / project_id / "sources"
            source_dir.mkdir(parents=True, exist_ok=True)
            token = uuid4().hex[:16]
            suffix = f".{document_format}"
            temporary_path = source_dir / f".upload_{token}{suffix}"
            document_path = source_dir / f"source_{token}{suffix}"
            try:
                temporary_path.write_bytes(content)
                temporary_path.replace(document_path)
            except OSError as exc:
                temporary_path.unlink(missing_ok=True)
                raise WorkflowError("source document could not be stored") from exc

            now = datetime.now(timezone.utc)
            document_id = f"source_{uuid4().hex[:16]}"
            document_sha256 = sha256_file(document_path)
            records: list[NarrativeSourceChapter] = []
            for offset, parsed in enumerate(parsed_chapters):
                source_input = NarrativeSourceChapterInput(
                    source_name=clean_source_name,
                    rights_basis=clean_rights_basis,
                    allow_external_processing=allow_external_processing,
                    chapter_number=chapter_number_start + offset,
                    title=parsed.title,
                    content=parsed.content,
                )
                records.append(
                    NarrativeSourceChapter(
                        **source_input.model_dump(mode="json", exclude={"chapter_id"}),
                        chapter_id=f"chapter_{uuid4().hex[:16]}",
                        revision=1,
                        content_sha256=self._source_chapter_sha256(source_input),
                        source_document_id=document_id,
                        source_document_name=clean_name,
                        source_document_sha256=document_sha256,
                        source_document_format=document_format,
                        source_document_locator=parsed.locator,
                        created_at=now,
                        updated_at=now,
                    )
                )
            document = NarrativeSourceDocument(
                document_id=document_id,
                name=clean_name,
                format=document_format,
                source_name=clean_source_name,
                rights_basis=clean_rights_basis,
                allow_external_processing=allow_external_processing,
                sha256=document_sha256,
                size_bytes=len(content),
                parser_version=PARSER_VERSION,
                extraction_method=extraction.method,
                ocr_processor=extraction.processor,
                chapter_ids=[record.chapter_id for record in records],
                uri=str(document_path),
                imported_at=now,
            )
            project.source_documents.append(document)
            project.source_chapters.extend(records)
            self._record_event(
                project,
                action="source_document.imported",
                actor=actor,
                message=(
                    f"Source document {document.document_id} imported as "
                    f"{len(records)} chapters."
                ),
                details={
                    "document_id": document.document_id,
                    "name": document.name,
                    "format": document.format,
                    "sha256": document.sha256,
                    "size_bytes": document.size_bytes,
                    "chapter_ids": document.chapter_ids,
                    "parser_version": document.parser_version,
                    "extraction_method": document.extraction_method,
                    "ocr_processor": document.ocr_processor,
                    "allow_external_processing": document.allow_external_processing,
                },
            )
            self._persist()
            return {
                "document": self._source_document_view(document),
                "chapters": [self._source_chapter_view(record) for record in records],
            }

    def source_document_download(
        self,
        project_id: str,
        document_id: str,
    ) -> tuple[NarrativeSourceDocument, Path]:
        project = self._project(project_id)
        document = self._source_document(project, document_id)
        return document, self._verified_source_document_path(project_id, document)

    def _verified_source_document_path(
        self,
        project_id: str,
        document: NarrativeSourceDocument,
    ) -> Path:
        path = Path(document.uri)
        source_dir = (self.output_root / project_id / "sources").resolve()
        try:
            resolved = path.resolve(strict=True)
        except OSError as exc:
            raise WorkflowError("source document file is unavailable") from exc
        if not resolved.is_relative_to(source_dir) or not resolved.is_file():
            raise WorkflowError("source document file is unavailable")
        if sha256_file(resolved) != document.sha256:
            raise WorkflowError("source document integrity check failed")
        if resolved.stat().st_size != document.size_bytes:
            raise WorkflowError("source document size check failed")
        return resolved

    def update_source_chapter(
        self,
        project_id: str,
        chapter_id: str,
        *,
        changes: dict[str, Any],
        expected_revision: int | None = None,
        actor: str = "story-editor",
    ) -> dict[str, Any]:
        with self.planning.mutation_guard(project_id):
            project = self._project(project_id)
            self._assert_narrative_events_mutable(project)
            index = next(
                (
                    position
                    for position, item in enumerate(project.source_chapters)
                    if item.chapter_id == chapter_id
                ),
                None,
            )
            if index is None:
                raise WorkflowError(f"source chapter not found: {chapter_id}")
            current = project.source_chapters[index]
            if expected_revision is not None and current.revision != expected_revision:
                raise WorkflowError(
                    "source chapter revision conflict; refresh before saving"
                )
            allowed = set(NarrativeSourceChapterInput.model_fields) - {"chapter_id"}
            clean_changes = {key: value for key, value in changes.items() if key in allowed}
            unknown = set(changes) - allowed
            if unknown:
                raise WorkflowError(
                    "unsupported source chapter fields: " + ", ".join(sorted(unknown))
                )
            if not clean_changes:
                return {
                    "chapter": self._source_chapter_view(current),
                    "plan_invalidated": False,
                    "stale_candidate_count": 0,
                    "returned_event_count": 0,
                }
            source_input = NarrativeSourceChapterInput.model_validate(
                {
                    **current.model_dump(
                        mode="json",
                        exclude={
                            "revision",
                            "content_sha256",
                            "source_document_id",
                            "source_document_name",
                            "source_document_sha256",
                            "source_document_format",
                            "source_document_locator",
                            "created_at",
                            "updated_at",
                        },
                    ),
                    **clean_changes,
                }
            )
            now = datetime.now(timezone.utc)
            revised = NarrativeSourceChapter(
                **source_input.model_dump(mode="json", exclude={"chapter_id"}),
                chapter_id=current.chapter_id,
                revision=current.revision + 1,
                content_sha256=self._source_chapter_sha256(source_input),
                source_document_id=current.source_document_id,
                source_document_name=current.source_document_name,
                source_document_sha256=current.source_document_sha256,
                source_document_format=current.source_document_format,
                source_document_locator=current.source_document_locator,
                created_at=current.created_at,
                updated_at=now,
            )
            project.source_chapters[index] = revised

            stale_candidate_ids: list[str] = []
            for candidate_index, candidate in enumerate(project.narrative_event_candidates):
                if (
                    candidate.source_chapter_id == chapter_id
                    and candidate.status == NarrativeCandidateStatus.PENDING
                ):
                    project.narrative_event_candidates[candidate_index] = candidate.model_copy(
                        update={
                            "status": NarrativeCandidateStatus.STALE,
                            "revision": candidate.revision + 1,
                            "updated_at": now,
                        }
                    )
                    stale_candidate_ids.append(candidate.candidate_id)

            returned_event_ids: list[str] = []
            for event_index, event in enumerate(project.narrative_events):
                if (
                    event.source_chapter_id == chapter_id
                    and event.review_status != ReviewStatus.CHANGES_REQUESTED
                ):
                    project.narrative_events[event_index] = event.model_copy(
                        update={
                            "review_status": ReviewStatus.CHANGES_REQUESTED,
                            "revision": event.revision + 1,
                            "updated_at": now,
                        }
                    )
                    returned_event_ids.append(event.event_id)
            returned_scene_ids = self._return_adaptation_scenes_for_event_ids(
                project,
                returned_event_ids,
                now=now,
            )
            invalidated = (
                self._invalidate_plan_for_narrative_change(project)
                if returned_event_ids or returned_scene_ids
                else False
            )
            self._record_event(
                project,
                action="source_chapter.updated",
                actor=actor,
                message=f"Source chapter {chapter_id} revised to version {revised.revision}.",
                details={
                    "chapter_id": chapter_id,
                    "revision": revised.revision,
                    "fields": sorted(clean_changes),
                    "content_sha256": revised.content_sha256,
                    "stale_candidate_ids": stale_candidate_ids,
                    "returned_event_ids": returned_event_ids,
                    "returned_scene_ids": returned_scene_ids,
                    "plan_invalidated": invalidated,
                },
            )
            if invalidated:
                self._record_event(
                    project,
                    action="project.plan_invalidated",
                    actor=actor,
                    message="Existing plan invalidated by a source chapter change.",
                    details={"reason": "source_chapter.updated", "chapter_id": chapter_id},
                )
            self._persist()
            return {
                "chapter": self._source_chapter_view(revised),
                "plan_invalidated": invalidated,
                "stale_candidate_count": len(stale_candidate_ids),
                "returned_event_count": len(returned_event_ids),
                "returned_scene_count": len(returned_scene_ids),
            }

    def extract_narrative_event_candidates(
        self,
        project_id: str,
        chapter_id: str,
        *,
        actor: str = "story-editor",
    ) -> dict[str, Any]:
        with self.planning.mutation_guard(project_id):
            project = self._project(project_id)
            self._assert_narrative_events_mutable(project)
            chapter = self._source_chapter(project, chapter_id)
            now = datetime.now(timezone.utc)
            superseded_ids: list[str] = []
            for index, candidate in enumerate(project.narrative_event_candidates):
                if (
                    candidate.source_chapter_id == chapter_id
                    and candidate.status == NarrativeCandidateStatus.PENDING
                ):
                    project.narrative_event_candidates[index] = candidate.model_copy(
                        update={
                            "status": NarrativeCandidateStatus.STALE,
                            "revision": candidate.revision + 1,
                            "updated_at": now,
                        }
                    )
                    superseded_ids.append(candidate.candidate_id)
            proposals, extractor, extraction_model, fallback_reason = (
                self._extract_narrative_event_proposals(project, chapter)
            )
            candidates = [
                NarrativeEventCandidate(
                    candidate_id=f"candidate_{uuid4().hex[:16]}",
                    source_chapter_id=chapter.chapter_id,
                    source_chapter_revision=chapter.revision,
                    source_content_sha256=chapter.content_sha256,
                    sequence=sequence,
                    proposal=proposal,
                    extractor=extractor,
                    extraction_model=extraction_model,
                    created_at=now,
                    updated_at=now,
                )
                for sequence, proposal in enumerate(proposals, start=1)
            ]
            project.narrative_event_candidates.extend(candidates)
            self._record_event(
                project,
                action="narrative_candidate.extracted",
                actor=actor,
                message=(
                    f"Extracted {len(candidates)} narrative candidates from "
                    f"source chapter {chapter_id}."
                ),
                details={
                    "chapter_id": chapter_id,
                    "chapter_revision": chapter.revision,
                    "content_sha256": chapter.content_sha256,
                    "candidate_ids": [candidate.candidate_id for candidate in candidates],
                    "extractor": extractor,
                    "extraction_model": extraction_model,
                    "superseded_candidate_ids": superseded_ids,
                    "fallback_reason": fallback_reason,
                },
            )
            self._persist()
            return {
                "chapter": self._source_chapter_view(chapter),
                "extractor": extractor,
                "extraction_model": extraction_model,
                "fallback_reason": fallback_reason,
                "candidates": [
                    self._narrative_event_candidate_view(candidate)
                    for candidate in candidates
                ],
            }

    def adopt_narrative_event_candidate(
        self,
        project_id: str,
        candidate_id: str,
        *,
        characters: list[str] | None = None,
        event_id: str | None = None,
        expected_revision: int | None = None,
        actor: str = "story-editor",
    ) -> dict[str, Any]:
        with self.planning.mutation_guard(project_id):
            project = self._project(project_id)
            self._assert_narrative_events_mutable(project)
            candidate_index = next(
                (
                    index
                    for index, item in enumerate(project.narrative_event_candidates)
                    if item.candidate_id == candidate_id
                ),
                None,
            )
            if candidate_index is None:
                raise WorkflowError(f"narrative candidate not found: {candidate_id}")
            candidate = project.narrative_event_candidates[candidate_index]
            if expected_revision is not None and candidate.revision != expected_revision:
                raise WorkflowError(
                    "narrative candidate revision conflict; refresh before adopting"
                )
            if candidate.status != NarrativeCandidateStatus.PENDING:
                raise WorkflowError("only pending narrative candidates can be adopted")
            chapter = self._source_chapter(project, candidate.source_chapter_id)
            if (
                candidate.source_chapter_revision != chapter.revision
                or candidate.source_content_sha256 != chapter.content_sha256
            ):
                raise WorkflowError(
                    "narrative candidate is stale; extract candidates from the current chapter"
                )
            selected_characters = characters if characters is not None else candidate.proposal.characters
            if not selected_characters:
                raise WorkflowError(
                    "select at least one declared project character before adopting a candidate"
                )
            event_input = NarrativeEventInput(
                event_id=event_id,
                chapter_number=chapter.chapter_number,
                sequence=candidate.sequence,
                title=candidate.proposal.title,
                scene=candidate.proposal.scene,
                summary=candidate.proposal.summary,
                characters=selected_characters,
                importance=candidate.proposal.importance,
                emotions=candidate.proposal.emotions,
                estimated_duration_seconds=candidate.proposal.estimated_duration_seconds,
                source_chapter_id=chapter.chapter_id,
                source_locator=candidate.proposal.source_locator,
                source_excerpt=candidate.proposal.source_excerpt,
            )
            self._validate_narrative_event_characters(project, event_input)
            resolved_event_id = event_input.event_id or f"event_{uuid4().hex[:16]}"
            if any(item.event_id == resolved_event_id for item in project.narrative_events):
                raise WorkflowError(f"narrative event already exists: {resolved_event_id}")
            now = datetime.now(timezone.utc)
            event = NarrativeEvent(
                **event_input.model_dump(mode="json", exclude={"event_id"}),
                event_id=resolved_event_id,
                revision=1,
                review_status=ReviewStatus.PENDING,
                source_sha256=self._narrative_source_sha256(event_input),
                source_chapter_content_sha256=chapter.content_sha256,
                created_at=now,
                updated_at=now,
            )
            project.narrative_events.append(event)
            adopted = candidate.model_copy(
                update={
                    "status": NarrativeCandidateStatus.ADOPTED,
                    "revision": candidate.revision + 1,
                    "adopted_event_id": event.event_id,
                    "updated_at": now,
                }
            )
            project.narrative_event_candidates[candidate_index] = adopted
            invalidated = self._invalidate_plan_for_narrative_change(project)
            self._record_event(
                project,
                action="narrative_event.created",
                actor=actor,
                message=f"Narrative event {event.event_id} created from candidate {candidate_id}.",
                details={
                    "event_id": event.event_id,
                    "candidate_id": candidate_id,
                    "chapter_id": chapter.chapter_id,
                    "source_sha256": event.source_sha256,
                    "plan_invalidated": invalidated,
                },
            )
            self._record_event(
                project,
                action="narrative_candidate.adopted",
                actor=actor,
                message=f"Narrative candidate {candidate_id} adopted as {event.event_id}.",
                details={
                    "candidate_id": candidate_id,
                    "candidate_revision": adopted.revision,
                    "event_id": event.event_id,
                    "chapter_id": chapter.chapter_id,
                },
            )
            if invalidated:
                self._record_event(
                    project,
                    action="project.plan_invalidated",
                    actor=actor,
                    message="Existing plan invalidated by a narrative candidate adoption.",
                    details={"reason": "narrative_candidate.adopted", "candidate_id": candidate_id},
                )
            self._persist()
            return {
                "candidate": self._narrative_event_candidate_view(adopted),
                "event": self._narrative_event_view(event),
                "plan_invalidated": invalidated,
            }

    def discard_narrative_event_candidate(
        self,
        project_id: str,
        candidate_id: str,
        *,
        reason: str = "",
        expected_revision: int | None = None,
        actor: str = "story-editor",
    ) -> dict[str, Any]:
        with self.planning.mutation_guard(project_id):
            project = self._project(project_id)
            self._assert_narrative_events_mutable(project)
            index = next(
                (
                    position
                    for position, item in enumerate(project.narrative_event_candidates)
                    if item.candidate_id == candidate_id
                ),
                None,
            )
            if index is None:
                raise WorkflowError(f"narrative candidate not found: {candidate_id}")
            current = project.narrative_event_candidates[index]
            if expected_revision is not None and current.revision != expected_revision:
                raise WorkflowError(
                    "narrative candidate revision conflict; refresh before discarding"
                )
            if current.status != NarrativeCandidateStatus.PENDING:
                raise WorkflowError("only pending narrative candidates can be discarded")
            revised = current.model_copy(
                update={
                    "status": NarrativeCandidateStatus.DISCARDED,
                    "revision": current.revision + 1,
                    "discarded_reason": reason.strip() or None,
                    "updated_at": datetime.now(timezone.utc),
                }
            )
            project.narrative_event_candidates[index] = revised
            self._record_event(
                project,
                action="narrative_candidate.discarded",
                actor=actor,
                message=f"Narrative candidate {candidate_id} discarded.",
                details={
                    "candidate_id": candidate_id,
                    "revision": revised.revision,
                    "reason": revised.discarded_reason,
                },
            )
            self._persist()
            return {"candidate": self._narrative_event_candidate_view(revised)}

    def create_narrative_event(
        self,
        project_id: str,
        event: NarrativeEventInput,
        *,
        actor: str = "story-editor",
    ) -> dict[str, Any]:
        with self.planning.mutation_guard(project_id):
            project = self._project(project_id)
            self._assert_narrative_events_mutable(project)
            input_event = NarrativeEventInput.model_validate(
                {
                    name: getattr(event, name)
                    for name in NarrativeEventInput.model_fields
                }
            )
            self._validate_narrative_event_characters(project, input_event)
            event_id = input_event.event_id or f"event_{uuid4().hex[:16]}"
            if any(item.event_id == event_id for item in project.narrative_events):
                raise WorkflowError(f"narrative event already exists: {event_id}")
            now = datetime.now(timezone.utc)
            record = NarrativeEvent(
                **input_event.model_dump(mode="json", exclude={"event_id"}),
                event_id=event_id,
                revision=1,
                review_status=ReviewStatus.PENDING,
                source_sha256=self._narrative_source_sha256(input_event),
                source_chapter_content_sha256=self._narrative_event_source_chapter_sha(
                    project,
                    input_event,
                ),
                created_at=now,
                updated_at=now,
            )
            project.narrative_events.append(record)
            invalidated = self._invalidate_plan_for_narrative_change(project)
            self._record_event(
                project,
                action="narrative_event.created",
                actor=actor,
                message=f"Narrative event {record.event_id} created.",
                details={
                    "event_id": record.event_id,
                    "revision": record.revision,
                    "chapter_number": record.chapter_number,
                    "sequence": record.sequence,
                    "source_sha256": record.source_sha256,
                    "plan_invalidated": invalidated,
                },
            )
            if invalidated:
                self._record_event(
                    project,
                    action="project.plan_invalidated",
                    actor=actor,
                    message="Existing plan invalidated by a narrative event change.",
                    details={"reason": "narrative_event.created"},
                )
            self._persist()
            return {
                "event": self._narrative_event_view(record),
                "plan_invalidated": invalidated,
            }

    def update_narrative_event(
        self,
        project_id: str,
        event_id: str,
        *,
        changes: dict[str, Any],
        expected_revision: int | None = None,
        actor: str = "story-editor",
    ) -> dict[str, Any]:
        with self.planning.mutation_guard(project_id):
            project = self._project(project_id)
            self._assert_narrative_events_mutable(project)
            index = next(
                (
                    index
                    for index, item in enumerate(project.narrative_events)
                    if item.event_id == event_id
                ),
                None,
            )
            if index is None:
                raise WorkflowError(f"narrative event not found: {event_id}")
            current = project.narrative_events[index]
            if expected_revision is not None and current.revision != expected_revision:
                raise WorkflowError(
                    "narrative event revision conflict; refresh before saving"
                )
            allowed = set(NarrativeEventInput.model_fields) - {"event_id"}
            clean_changes = {
                key: value
                for key, value in changes.items()
                if key in allowed
            }
            unknown = set(changes) - allowed
            if unknown:
                raise WorkflowError(
                    "unsupported narrative event fields: "
                    + ", ".join(sorted(unknown))
                )
            if not clean_changes:
                return {"event": self._narrative_event_view(current), "plan_invalidated": False}
            candidate_input = NarrativeEventInput.model_validate(
                {
                    **current.model_dump(mode="json", exclude={
                        "revision",
                        "review_status",
                        "source_sha256",
                        "source_chapter_content_sha256",
                        "created_at",
                        "updated_at",
                    }),
                    **clean_changes,
                }
            )
            self._validate_narrative_event_characters(project, candidate_input)
            now = datetime.now(timezone.utc)
            candidate = NarrativeEvent(
                **candidate_input.model_dump(mode="json", exclude={"event_id"}),
                event_id=current.event_id,
                revision=current.revision + 1,
                review_status=ReviewStatus.PENDING,
                source_sha256=self._narrative_source_sha256(candidate_input),
                source_chapter_content_sha256=self._narrative_event_source_chapter_sha(
                    project,
                    candidate_input,
                ),
                created_at=current.created_at,
                updated_at=now,
            )
            project.narrative_events[index] = candidate
            returned_scene_ids = self._return_adaptation_scenes_for_event_ids(
                project,
                [event_id],
                now=now,
            )
            invalidated = self._invalidate_plan_for_narrative_change(project)
            self._record_event(
                project,
                action="narrative_event.updated",
                actor=actor,
                message=f"Narrative event {event_id} revised to version {candidate.revision}.",
                details={
                    "event_id": event_id,
                    "revision": candidate.revision,
                    "fields": sorted(clean_changes),
                    "source_sha256": candidate.source_sha256,
                    "returned_scene_ids": returned_scene_ids,
                    "plan_invalidated": invalidated,
                },
            )
            if invalidated:
                self._record_event(
                    project,
                    action="project.plan_invalidated",
                    actor=actor,
                    message="Existing plan invalidated by a narrative event change.",
                    details={"reason": "narrative_event.updated", "event_id": event_id},
                )
            self._persist()
            return {
                "event": self._narrative_event_view(candidate),
                "plan_invalidated": invalidated,
            }

    def review_narrative_event(
        self,
        project_id: str,
        event_id: str,
        *,
        status: ReviewStatus,
        comment: str = "",
        expected_revision: int | None = None,
        actor: str = "story-reviewer",
    ) -> dict[str, Any]:
        if status not in {ReviewStatus.APPROVED, ReviewStatus.CHANGES_REQUESTED}:
            raise WorkflowError("narrative event review must approve or request changes")
        with self.planning.mutation_guard(project_id):
            project = self._project(project_id)
            self._assert_narrative_events_mutable(project)
            index = next(
                (
                    index
                    for index, item in enumerate(project.narrative_events)
                    if item.event_id == event_id
                ),
                None,
            )
            if index is None:
                raise WorkflowError(f"narrative event not found: {event_id}")
            current = project.narrative_events[index]
            if expected_revision is not None and current.revision != expected_revision:
                raise WorkflowError(
                    "narrative event revision conflict; refresh before reviewing"
                )
            if status == ReviewStatus.APPROVED:
                self._assert_narrative_event_source_current(project, current)
            if current.review_status == status:
                return {"event": self._narrative_event_view(current), "plan_invalidated": False}
            revised = current.model_copy(
                update={
                    "review_status": status,
                    "updated_at": datetime.now(timezone.utc),
                }
            )
            project.narrative_events[index] = revised
            returned_scene_ids = self._return_adaptation_scenes_for_event_ids(
                project,
                [event_id],
                now=revised.updated_at,
            )
            invalidated = self._invalidate_plan_for_narrative_change(project)
            self._record_event(
                project,
                action="narrative_event.reviewed",
                actor=actor,
                message=f"Narrative event {event_id} reviewed as {status}.",
                details={
                    "event_id": event_id,
                    "revision": revised.revision,
                    "status": status,
                    "comment": comment,
                    "returned_scene_ids": returned_scene_ids,
                    "plan_invalidated": invalidated,
                },
            )
            if invalidated:
                self._record_event(
                    project,
                    action="project.plan_invalidated",
                    actor=actor,
                    message="Existing plan invalidated by a narrative event review.",
                    details={"reason": "narrative_event.reviewed", "event_id": event_id},
                )
            self._persist()
            return {
                "event": self._narrative_event_view(revised),
                "plan_invalidated": invalidated,
            }

    def update_shot_card(
        self,
        project_id: str,
        shot_id: str,
        *,
        changes: dict[str, Any],
        actor: str = "studio-editor",
    ) -> dict[str, Any]:
        project = self._project(project_id)
        self._ensure_active(project)
        runtime = self._shot(project, shot_id)
        if runtime.current_job_id:
            try:
                job = self.jobs.get(runtime.current_job_id)
            except KeyError:
                job = None
            if job and job.status in {JobStatus.QUEUED, JobStatus.ADMITTED, JobStatus.RUNNING}:
                raise WorkflowError("cannot edit a shot with an active generation job")
        clean_changes = {key: value for key, value in changes.items() if key != "actor"}
        allowed = {
            "scene",
            "description",
            "characters",
            "duration_seconds",
            "mood",
            "subtitle_text",
        }
        unknown = set(clean_changes) - allowed
        if unknown:
            raise WorkflowError(f"unsupported shot fields: {', '.join(sorted(unknown))}")
        candidate = runtime.shot.model_copy(update=clean_changes)
        if any(character not in project.brief.characters for character in candidate.characters):
            raise WorkflowError("shot characters must be declared in the project brief")
        runtime.shot = candidate
        runtime.revision += 1
        runtime.spec = self._compile_spec(
            candidate,
            reference_assets=self._reference_assets_for_shot(project, candidate),
        )
        runtime.current_job_id = None
        runtime.current_artifact = None
        runtime.route = None
        runtime.quality = None
        runtime.review_status = ReviewStatus.PENDING
        self._invalidate_workflow_from(
            project,
            "storyboard",
            reason="shot_card_updated",
            actor=actor,
        )
        self._record_event(
            project,
            action="shot.card_updated",
            actor=actor,
            message=f"{shot_id} shot card updated.",
            shot_id=shot_id,
            details={"fields": sorted(clean_changes), "revision": runtime.revision},
        )
        self._persist()
        return self.shot_view(runtime)

    def tenant_cost_report(
        self,
        tenant_id: str | None,
        *,
        include_archived: bool = False,
    ) -> dict[str, Any]:
        """Return a tenant-scoped, exportable cost ledger."""
        clean_tenant = str(tenant_id or "default")
        projects = sorted(
            [
                project
                for project in self.projects.values()
                if project.brief.tenant_id == clean_tenant
                and (include_archived or project.archived_at is None)
            ],
            key=lambda project: project.created_at,
        )
        project_rows: list[dict[str, Any]] = []
        ledger: list[dict[str, Any]] = []
        for project in projects:
            cost = self._cost_report(project)
            project_rows.append(
                {
                    "project_id": project.brief.project_id,
                    "title": project.brief.title,
                    "status": project.status,
                    "archived": project.archived_at is not None,
                    "budget": cost["budget"],
                    "spent": cost["spent"],
                    "remaining": cost["remaining"],
                    "overspend": cost["overspend"],
                }
            )
            for item in cost["shot_breakdown"]:
                ledger.append(
                    {
                        "project_id": project.brief.project_id,
                        "title": project.brief.title,
                        "shot_id": item["shot_id"],
                        "revision": item["revision"],
                        "review_status": item["review_status"],
                        "attempts": item["attempts"],
                        "variant_attempts": item["variant_attempts"],
                        "spent": item["spent"],
                        "last_estimated_cost": item["last_estimated_cost"],
                    }
                )
        budget = round(sum(float(row["budget"]) for row in project_rows), 4)
        spent = round(sum(float(row["spent"]) for row in project_rows), 4)
        return {
            "schema_version": "mediaforge-tenant-cost-ledger-v1",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "tenant_id": clean_tenant,
            "include_archived": include_archived,
            "summary": {
                "project_count": len(project_rows),
                "budget": budget,
                "spent": spent,
                "remaining": round(max(budget - spent, 0.0), 4),
                "overspend": round(max(spent - budget, 0.0), 4),
                "spent_ratio": round(spent / budget, 4) if budget else None,
            },
            "projects": project_rows,
            "ledger": ledger,
        }

    def export_tenant_cost_report(
        self,
        tenant_id: str | None,
        *,
        file_format: str = "json",
        include_archived: bool = False,
    ) -> Path:
        if file_format not in {"json", "csv"}:
            raise WorkflowError("tenant cost format must be json or csv")
        report = self.tenant_cost_report(
            tenant_id,
            include_archived=include_archived,
        )
        output_dir = self.output_root / "studio"
        output_dir.mkdir(parents=True, exist_ok=True)
        clean_tenant = "".join(
            char if char.isalnum() or char in {"-", "_"} else "_"
            for char in str(report["tenant_id"])
        ) or "default"
        scope = "all" if include_archived else "active"
        path = output_dir / f"tenant-cost-{clean_tenant}-{scope}.{file_format}"
        if file_format == "json":
            path.write_text(
                json.dumps(report, ensure_ascii=True, indent=2),
                encoding="utf-8",
            )
            return path
        with path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(
                handle,
                fieldnames=[
                    "project_id",
                    "title",
                    "shot_id",
                    "revision",
                    "review_status",
                    "attempts",
                    "variant_attempts",
                    "spent",
                    "last_estimated_cost",
                ],
            )
            writer.writeheader()
            writer.writerows(report["ledger"])
        return path

    @staticmethod
    def project_role_levels() -> dict[str, int]:
        return {
            "viewer": 10,
            "reviewer": 20,
            "editor": 30,
            "publisher": 40,
            "owner": 50,
        }

    def project_member_role(self, project_id: str, subject: str) -> str | None:
        project = self._project(project_id)
        member = next(
            (
                item
                for item in project.members
                if item.get("subject") == subject
            ),
            None,
        )
        return str(member.get("role")) if member else None

    def required_project_role(self, *, method: str, path: str) -> str | None:
        """Map a project operation to its minimum project membership role."""
        if path.endswith("/callback"):
            return None
        if "/collaboration/members" in path:
            return "owner"
        if "/comments" in path:
            return "reviewer"
        if "/review" in path or path.endswith("/approve-ready"):
            return "reviewer"
        if "/audit/anchor" in path:
            return "publisher"
        if any(marker in path for marker in ("/release", "/deliveries", "/closeout", "/archive")):
            return "publisher"
        if method.upper() in {"GET", "HEAD", "OPTIONS"}:
            return "viewer"
        return "editor"

    def _enforce_project_quota(
        self,
        tenant_id: str | None,
        *,
        additional_budget: float = 0.0,
    ) -> None:
        report = self.tenant_quota(tenant_id)
        quota = report["quota"]
        usage = report["usage"]
        requested = {
            "projects": usage["project_count"] + 1,
            "budget": round(usage["allocated_budget"] + additional_budget, 4),
        }
        if quota["max_projects"] is not None and requested["projects"] > quota["max_projects"]:
            raise QuotaViolation(
                "tenant project quota exceeded",
                report={**report, "resource": "projects", "requested": requested},
            )
        if quota["max_budget"] is not None and requested["budget"] > quota["max_budget"]:
            raise QuotaViolation(
                "tenant allocated budget quota exceeded",
                report={**report, "resource": "budget", "requested": requested},
            )

    def _enforce_job_quota(
        self,
        tenant_id: str | None,
        *,
        additional_jobs: int = 1,
    ) -> None:
        report = self.tenant_quota(tenant_id)
        max_jobs = report["quota"]["max_jobs"]
        requested = report["usage"]["job_count"] + additional_jobs
        if max_jobs is not None and requested > max_jobs:
            raise QuotaViolation(
                "tenant generation job quota exceeded",
                report={
                    **report,
                    "resource": "jobs",
                    "requested_jobs": requested,
                },
            )

    def project_collaboration(self, project_id: str) -> dict[str, Any]:
        project = self._project(project_id)
        members = sorted(
            (copy.deepcopy(member) for member in project.members),
            key=lambda member: (member.get("role") != "owner", member.get("subject", "")),
        )
        comments = sorted(
            (copy.deepcopy(comment) for comment in project.comments),
            key=lambda comment: comment.get("created_at", ""),
            reverse=True,
        )
        presence = self._active_collaboration_records(project.collaboration_presence)
        locks = self._active_collaboration_records(project.edit_locks)
        return {
            "schema_version": "mediaforge-project-collaboration-v2",
            "project_id": project_id,
            "members": members,
            "comments": comments,
            "presence": presence,
            "edit_locks": locks,
            "member_count": len(members),
            "comment_count": len(comments),
            "active_member_count": len(presence),
            "active_lock_count": len(locks),
            "document_count": len(project.collaboration_documents),
            "event_cursor": project.collaboration_event_sequence,
        }

    @staticmethod
    def _collaboration_document_key(value: str) -> str:
        clean = str(value or "").strip().lower()
        if not re.fullmatch(r"[a-z][a-z0-9_.:-]{0,159}", clean):
            raise WorkflowError("collaboration document_id is invalid")
        return clean

    def _collaboration_document_view(
        self,
        project: ProjectRuntime,
        document_id: str,
    ) -> dict[str, Any]:
        state = project.collaboration_documents.get(document_id)
        if state is None:
            state = empty_collaboration_document()
            project.collaboration_documents[document_id] = state
        try:
            text = collaboration_document_text(state)
        except CollaborationDocumentError as exc:
            raise WorkflowError(str(exc)) from exc
        return {
            "document_id": document_id,
            "text": text,
            "operation_count": int(state.get("operation_count") or 0),
            "node_count": len(state.get("nodes") or {}),
        }

    def collaboration_documents(self, project_id: str) -> dict[str, Any]:
        project = self._project(project_id)
        documents = [
            self._collaboration_document_view(project, document_id)
            for document_id in sorted(project.collaboration_documents)
        ]
        return {
            "schema_version": "mediaforge-collaboration-documents-v1",
            "project_id": project_id,
            "documents": documents,
            "event_cursor": project.collaboration_event_sequence,
        }

    def _record_collaboration_event(
        self,
        project: ProjectRuntime,
        *,
        event_type: str,
        actor: str,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        project.collaboration_event_sequence += 1
        event = {
            "event_id": project.collaboration_event_sequence,
            "type": event_type,
            "actor": actor,
            "occurred_at": datetime.now(timezone.utc).isoformat(),
            "payload": copy.deepcopy(payload or {}),
        }
        project.collaboration_events.append(event)
        project.collaboration_events = project.collaboration_events[-1_000:]
        return event

    def collaboration_events(
        self,
        project_id: str,
        *,
        after: int = 0,
    ) -> dict[str, Any]:
        project = self._project(project_id)
        cursor = max(0, int(after))
        events = [
            copy.deepcopy(event)
            for event in project.collaboration_events
            if int(event.get("event_id") or 0) > cursor
        ]
        return {
            "schema_version": "mediaforge-collaboration-events-v1",
            "project_id": project_id,
            "after": cursor,
            "cursor": project.collaboration_event_sequence,
            "events": events,
        }

    def collaboration_realtime_snapshot(self, project_id: str) -> dict[str, Any]:
        return {
            "schema_version": "mediaforge-collaboration-realtime-v1",
            "project_id": project_id,
            "collaboration": self.project_collaboration(project_id),
            "documents": self.collaboration_documents(project_id),
        }

    def apply_collaboration_text_operations(
        self,
        project_id: str,
        document_id: str,
        *,
        operations: list[dict[str, Any]],
        actor: str,
    ) -> dict[str, Any]:
        project = self._project(project_id)
        self._ensure_active(project)
        clean_document_id = self._collaboration_document_key(document_id)
        clean_actor = str(actor or "").strip()
        if not clean_actor or len(clean_actor) > 160:
            raise WorkflowError("collaboration document actor is required and must be <= 160 characters")
        try:
            state, changed = apply_collaboration_operations(
                project.collaboration_documents.get(clean_document_id), operations
            )
            project.collaboration_documents[clean_document_id] = state
            document = self._collaboration_document_view(project, clean_document_id)
        except CollaborationDocumentError as exc:
            raise WorkflowError(str(exc)) from exc
        if changed:
            self._record_event(
                project,
                action="collaboration.document_operations_applied",
                actor=clean_actor,
                message="Collaborative document operations applied.",
                details={
                    "document_id": clean_document_id,
                    "operation_count": len(operations),
                    "changed_count": changed,
                },
            )
            self._record_collaboration_event(
                project,
                event_type="document.updated",
                actor=clean_actor,
                payload={
                    "document_id": clean_document_id,
                    "operation_count": document["operation_count"],
                    "text": document["text"],
                },
            )
            self._persist()
        return {"project_id": project_id, "document": document, "changed_count": changed}

    def replace_collaboration_document(
        self,
        project_id: str,
        document_id: str,
        *,
        text: str,
        actor: str,
    ) -> dict[str, Any]:
        project = self._project(project_id)
        clean_document_id = self._collaboration_document_key(document_id)
        try:
            operations = collaboration_replacement_operations(
                project.collaboration_documents.get(clean_document_id),
                str(text),
                actor=actor,
            )
        except CollaborationDocumentError as exc:
            raise WorkflowError(str(exc)) from exc
        return self.apply_collaboration_text_operations(
            project_id,
            clean_document_id,
            operations=operations,
            actor=actor,
        )

    @staticmethod
    def _active_collaboration_records(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
        now = datetime.now(timezone.utc)
        active = []
        for record in records:
            try:
                expires_at = datetime.fromisoformat(str(record.get("expires_at")))
                if expires_at.tzinfo is None:
                    continue
            except (TypeError, ValueError):
                continue
            if expires_at > now:
                active.append(copy.deepcopy(record))
        return sorted(active, key=lambda item: (item.get("target_type", ""), item.get("target_id", ""), item.get("subject", "")))

    def update_collaboration_presence(
        self,
        project_id: str,
        *,
        subject: str,
        status: str = "ACTIVE",
        section: str | None = None,
        ttl_seconds: int = 90,
    ) -> dict[str, Any]:
        project = self._project(project_id)
        self._ensure_active(project)
        clean_subject = subject.strip()
        clean_status = status.strip().upper()
        clean_section = (section or "").strip() or None
        if not clean_subject or len(clean_subject) > 160:
            raise WorkflowError("presence subject is required and must be <= 160 characters")
        if clean_status not in {"ACTIVE", "AWAY", "BUSY"}:
            raise WorkflowError("presence status must be ACTIVE, AWAY, or BUSY")
        if clean_section and len(clean_section) > 160:
            raise WorkflowError("presence section must be <= 160 characters")
        if not 15 <= ttl_seconds <= 600:
            raise WorkflowError("presence ttl_seconds must be between 15 and 600")
        now = datetime.now(timezone.utc)
        existing = next(
            (item for item in project.collaboration_presence if item.get("subject") == clean_subject),
            None,
        )
        record = {
            "subject": clean_subject,
            "status": clean_status,
            "section": clean_section,
            "updated_at": now.isoformat(),
            "expires_at": (now + timedelta(seconds=ttl_seconds)).isoformat(),
        }
        if existing:
            existing.update(record)
        else:
            project.collaboration_presence.append(record)
        project.collaboration_presence = self._active_collaboration_records(project.collaboration_presence)
        self._record_collaboration_event(
            project,
            event_type="presence.updated",
            actor=clean_subject,
            payload={"status": clean_status, "section": clean_section},
        )
        self._persist()
        return self.project_collaboration(project_id)

    def acquire_edit_lock(
        self,
        project_id: str,
        *,
        subject: str,
        target_type: str,
        target_id: str,
        ttl_seconds: int = 120,
    ) -> dict[str, Any]:
        project = self._project(project_id)
        self._ensure_active(project)
        clean_subject = subject.strip()
        clean_type = target_type.strip().lower()
        clean_target = target_id.strip()
        if not clean_subject or len(clean_subject) > 160:
            raise WorkflowError("lock subject is required and must be <= 160 characters")
        if clean_type not in {"brief", "story_bible", "narrative_event", "scene", "shot", "timeline", "prompt"}:
            raise WorkflowError("edit lock target_type is not supported")
        if not clean_target or len(clean_target) > 240:
            raise WorkflowError("edit lock target_id is required and must be <= 240 characters")
        if not 15 <= ttl_seconds <= 900:
            raise WorkflowError("edit lock ttl_seconds must be between 15 and 900")
        project.edit_locks = self._active_collaboration_records(project.edit_locks)
        existing = next(
            (
                item for item in project.edit_locks
                if item.get("target_type") == clean_type and item.get("target_id") == clean_target
            ),
            None,
        )
        if existing and existing.get("subject") != clean_subject:
            raise WorkflowError(
                f"edit lock is held by {existing.get('subject')} until {existing.get('expires_at')}"
            )
        now = datetime.now(timezone.utc)
        record = {
            "lock_id": existing.get("lock_id") if existing else f"lock_{uuid4().hex[:16]}",
            "subject": clean_subject,
            "target_type": clean_type,
            "target_id": clean_target,
            "acquired_at": existing.get("acquired_at") if existing else now.isoformat(),
            "updated_at": now.isoformat(),
            "expires_at": (now + timedelta(seconds=ttl_seconds)).isoformat(),
        }
        if existing:
            existing.update(record)
        else:
            project.edit_locks.append(record)
        self._record_event(
            project,
            action="collaboration.edit_lock_acquired",
            actor=clean_subject,
            message="Project edit lock acquired.",
            details={"lock_id": record["lock_id"], "target_type": clean_type, "target_id": clean_target},
        )
        self._record_collaboration_event(
            project,
            event_type="lock.acquired",
            actor=clean_subject,
            payload={"lock": record},
        )
        self._persist()
        return {"project_id": project_id, "lock": copy.deepcopy(record)}

    def release_edit_lock(
        self,
        project_id: str,
        lock_id: str,
        *,
        subject: str,
    ) -> dict[str, Any]:
        project = self._project(project_id)
        self._ensure_active(project)
        clean_lock_id = lock_id.strip()
        lock = next((item for item in project.edit_locks if item.get("lock_id") == clean_lock_id), None)
        if lock is None:
            raise WorkflowError("edit lock was not found")
        if lock.get("subject") != subject.strip():
            raise WorkflowError("only the lock holder can release this edit lock")
        project.edit_locks.remove(lock)
        self._record_event(
            project,
            action="collaboration.edit_lock_released",
            actor=subject.strip(),
            message="Project edit lock released.",
            details={"lock_id": clean_lock_id, "target_type": lock.get("target_type"), "target_id": lock.get("target_id")},
        )
        self._record_collaboration_event(
            project,
            event_type="lock.released",
            actor=subject.strip(),
            payload={"lock_id": clean_lock_id},
        )
        self._persist()
        return self.project_collaboration(project_id)

    def upsert_project_member(
        self,
        project_id: str,
        *,
        subject: str,
        role: str = "viewer",
        actor: str = "studio-user",
    ) -> dict[str, Any]:
        project = self._project(project_id)
        self._ensure_active(project)
        clean_subject = subject.strip()
        clean_role = role.strip().lower()
        if not clean_subject or len(clean_subject) > 160:
            raise WorkflowError("member subject is required and must be <= 160 characters")
        if clean_role not in {"owner", "viewer", "editor", "reviewer", "publisher"}:
            raise WorkflowError("member role must be owner, viewer, editor, reviewer, or publisher")
        now = datetime.now(timezone.utc).isoformat()
        existing = next(
            (member for member in project.members if member.get("subject") == clean_subject),
            None,
        )
        if clean_role == "owner" and not existing:
            raise WorkflowError("a project can only have the original owner")
        if existing and existing.get("role") == "owner" and clean_role != "owner":
            raise WorkflowError("the project owner role cannot be downgraded")
        if existing:
            existing["role"] = clean_role
            existing["updated_at"] = now
        else:
            project.members.append(
                {"subject": clean_subject, "role": clean_role, "added_at": now}
            )
        self._record_event(
            project,
            action="collaboration.member_upserted",
            actor=actor,
            message=f"Project member {clean_subject} is {clean_role}.",
            details={"subject": clean_subject, "role": clean_role},
        )
        self._record_collaboration_event(
            project,
            event_type="member.updated",
            actor=actor,
            payload={"subject": clean_subject, "role": clean_role},
        )
        self._persist()
        return self.project_collaboration(project_id)

    def remove_project_member(
        self,
        project_id: str,
        subject: str,
        *,
        actor: str = "studio-user",
    ) -> dict[str, Any]:
        project = self._project(project_id)
        self._ensure_active(project)
        clean_subject = subject.strip()
        member = next(
            (member for member in project.members if member.get("subject") == clean_subject),
            None,
        )
        if member is None:
            raise WorkflowError(f"project member not found: {clean_subject}")
        if member.get("role") == "owner":
            raise WorkflowError("the project owner cannot be removed")
        project.members.remove(member)
        self._record_event(
            project,
            action="collaboration.member_removed",
            actor=actor,
            message=f"Project member {clean_subject} removed.",
            details={"subject": clean_subject, "role": member.get("role")},
        )
        self._record_collaboration_event(
            project,
            event_type="member.removed",
            actor=actor,
            payload={"subject": clean_subject},
        )
        self._persist()
        return self.project_collaboration(project_id)

    def add_project_comment(
        self,
        project_id: str,
        *,
        body: str,
        author: str,
        shot_id: str | None = None,
    ) -> dict[str, Any]:
        project = self._project(project_id)
        self._ensure_active(project)
        clean_body = body.strip()
        clean_author = author.strip()
        if not clean_body or len(clean_body) > 2000:
            raise WorkflowError("comment body is required and must be <= 2000 characters")
        if not clean_author or len(clean_author) > 160:
            raise WorkflowError("comment author is required and must be <= 160 characters")
        if shot_id:
            self._shot(project, shot_id)
        comment = {
            "comment_id": f"comment_{uuid4().hex[:16]}",
            "body": clean_body,
            "author": clean_author,
            "shot_id": shot_id,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        project.comments.append(comment)
        self._record_event(
            project,
            action="collaboration.comment_added",
            actor=clean_author,
            message="Project collaboration comment added.",
            shot_id=shot_id,
            details={"comment_id": comment["comment_id"]},
        )
        self._record_collaboration_event(
            project,
            event_type="comment.added",
            actor=clean_author,
            payload={"comment": comment},
        )
        self._persist()
        return {"project_id": project_id, "comment": copy.deepcopy(comment)}

    def remove_project_comment(
        self,
        project_id: str,
        comment_id: str,
        *,
        actor: str = "studio-user",
    ) -> dict[str, Any]:
        project = self._project(project_id)
        self._ensure_active(project)
        comment = next(
            (item for item in project.comments if item.get("comment_id") == comment_id),
            None,
        )
        if comment is None:
            raise WorkflowError(f"project comment not found: {comment_id}")
        project.comments.remove(comment)
        self._record_event(
            project,
            action="collaboration.comment_removed",
            actor=actor,
            message="Project collaboration comment removed.",
            shot_id=comment.get("shot_id"),
            details={"comment_id": comment_id},
        )
        self._record_collaboration_event(
            project,
            event_type="comment.removed",
            actor=actor,
            payload={"comment_id": comment_id, "shot_id": comment.get("shot_id")},
        )
        self._persist()
        return self.project_collaboration(project_id)

    def register_reference_asset(
        self,
        project_id: str,
        *,
        name: str,
        content_b64: str,
        license: str,
        kind: str = "character_reference",
        character: str | None = None,
        source: str = "studio-upload",
        actor: str = "studio-user",
    ) -> dict[str, Any]:
        """Store an image reference and bind the latest version to planned shots."""
        project = self._project(project_id)
        self._ensure_active(project)
        clean_name = name.strip()
        clean_license = license.strip()
        clean_source = source.strip()
        if not clean_name or not clean_license or not clean_source:
            raise WorkflowError("reference asset name, license, and source are required")
        if kind not in {
            "character_reference",
            "style_reference",
            "location_reference",
        }:
            raise WorkflowError(f"unsupported reference asset kind: {kind}")
        clean_character = character.strip() if character else None
        if kind == "character_reference":
            if not clean_character:
                raise WorkflowError("character reference requires a character")
            if clean_character not in project.brief.characters:
                raise WorkflowError(
                    f"character is not present in project brief: {clean_character}"
                )
        elif clean_character:
            raise WorkflowError("only character references may set character")
        try:
            content = base64.b64decode(content_b64, validate=True)
        except (ValueError, TypeError) as exc:
            raise WorkflowError("content_b64 is not valid base64") from exc
        if not content:
            raise WorkflowError("reference asset is empty")
        if len(content) > 10 * 1024 * 1024:
            raise WorkflowError("reference asset exceeds the 10 MB limit")

        reference_dir = self.output_root / project_id / "references"
        reference_dir.mkdir(parents=True, exist_ok=True)
        token = uuid4().hex[:16]
        temporary_path = reference_dir / f".upload_{token}"
        temporary_path.write_bytes(content)
        probe = probe_image(temporary_path)
        if not probe.valid or not probe.format:
            temporary_path.unlink(missing_ok=True)
            raise WorkflowError("reference asset must be a readable image")
        suffix = {
            "PNG": ".png",
            "JPEG": ".jpg",
            "WEBP": ".webp",
            "GIF": ".gif",
            "BMP": ".bmp",
            "TIFF": ".tiff",
        }.get(str(probe.format).upper(), ".img")
        asset_path = reference_dir / f"reference_{token}{suffix}"
        temporary_path.replace(asset_path)
        version_number = 1 + sum(
            1
            for asset in project.reference_assets
            if asset.get("character") == clean_character
        )
        asset = {
            "asset_id": f"{project_id}:reference:{token}",
            "kind": kind,
            "name": clean_name,
            "version": f"v{version_number}",
            "character": clean_character,
            "source": clean_source,
            "license": clean_license,
            "uri": str(asset_path),
            "mime_type": {
                "PNG": "image/png",
                "JPEG": "image/jpeg",
                "WEBP": "image/webp",
                "GIF": "image/gif",
                "BMP": "image/bmp",
                "TIFF": "image/tiff",
            }.get(str(probe.format).upper(), "application/octet-stream"),
            "sha256": sha256_file(asset_path),
            "size_bytes": asset_path.stat().st_size,
        }
        ReferenceAssetRef.model_validate(asset)
        project.reference_assets.append(asset)
        self._invalidate_workflow_from(
            project,
            "assets",
            reason="reference_asset_registered",
            actor=actor,
        )
        for runtime in project.shots.values():
            references = self._reference_assets_for_shot(project, runtime.shot)
            asset_versions = dict(runtime.spec.asset_versions)
            for reference in references:
                if reference.get("character"):
                    asset_versions[reference["character"]] = (
                        f"{reference['asset_id']}:{reference['version']}"
                    )
            runtime.spec = runtime.spec.model_copy(
                update={
                    "asset_versions": asset_versions,
                    "reference_assets": [
                        ReferenceAssetRef.model_validate(reference)
                        for reference in references
                    ],
                }
            )
        self._record_event(
            project,
            action="reference_asset.registered",
            actor=actor,
            message=f"Reference asset {clean_name} registered.",
            details={
                "asset_id": asset["asset_id"],
                "kind": kind,
                "character": clean_character,
                "license": clean_license,
                "sha256": asset["sha256"],
                "size_bytes": asset["size_bytes"],
            },
        )
        self._persist()
        return {
            "project_id": project_id,
            "asset": copy.deepcopy(asset),
            "reference_assets": self.asset_inventory(project_id)["reference_assets"],
        }

    def register_audio_track(
        self,
        project_id: str,
        *,
        name: str,
        content_b64: str,
        license: str = "user_supplied_or_project_owned",
        source: str = "studio-upload",
        actor: str = "studio-user",
    ) -> dict[str, Any]:
        """Store one optional soundtrack and invalidate stale postproduction outputs."""
        project = self._project(project_id)
        self._ensure_active(project)
        if project.release is not None:
            raise WorkflowError("released project audio cannot be changed")
        clean_name = Path(name.strip()).name
        clean_license = license.strip().lower()
        clean_source = source.strip()
        if not clean_license or not clean_source:
            raise WorkflowError("audio track license and source are required")
        suffix = Path(clean_name).suffix.lower()
        allowed_suffixes = {
            ".aac",
            ".flac",
            ".m4a",
            ".mp3",
            ".ogg",
            ".opus",
            ".wav",
            ".webm",
        }
        if not clean_name or suffix not in allowed_suffixes:
            raise WorkflowError(
                "audio track must use a supported extension: "
                ".aac, .flac, .m4a, .mp3, .ogg, .opus, .wav, or .webm"
            )
        try:
            content = base64.b64decode(content_b64, validate=True)
        except (ValueError, TypeError) as exc:
            raise WorkflowError("content_b64 is not valid base64") from exc
        if not content:
            raise WorkflowError("audio track is empty")
        if len(content) > 50 * 1024 * 1024:
            raise WorkflowError("audio track exceeds the 50 MB limit")

        audio_dir = self.output_root / project_id / "audio"
        audio_dir.mkdir(parents=True, exist_ok=True)
        token = uuid4().hex[:16]
        temporary_path = audio_dir / f".upload_{token}{suffix}"
        temporary_path.write_bytes(content)
        probe = probe_audio(temporary_path)
        if not probe.valid:
            temporary_path.unlink(missing_ok=True)
            raise WorkflowError("audio track must be a readable audio file")

        for existing in audio_dir.glob("soundtrack.*"):
            existing.unlink(missing_ok=True)
        audio_path = audio_dir / f"soundtrack{suffix}"
        temporary_path.replace(audio_path)
        previous_audio = project.audio_track
        project.audio_track = str(audio_path)
        project.dialogue_timeline = None
        project.audio_track_metadata = {
            "name": clean_name,
            "uri": project.audio_track,
            "format": probe.format,
            "duration_seconds": probe.duration_seconds,
            "license": clean_license,
            "source": clean_source,
            "size_bytes": audio_path.stat().st_size,
            "sha256": sha256_file(audio_path),
            "registered_at": datetime.now(timezone.utc).isoformat(),
        }
        self._invalidate_postproduction(project)
        self._record_event(
            project,
            action="audio_track.registered",
            actor=actor,
            message="Project soundtrack registered.",
            details={
                "audio_track": project.audio_track,
                "previous_audio_track": previous_audio,
                "duration_seconds": probe.duration_seconds,
                "license": clean_license,
                "source": clean_source,
                "size_bytes": project.audio_track_metadata["size_bytes"],
                "sha256": project.audio_track_metadata["sha256"],
            },
        )
        self._persist()
        return self.project_view(project_id)

    def generate_voiceover(
        self,
        project_id: str,
        *,
        text: str,
        voice: str = "default",
        language: str = "zh-CN",
        license: str = "unverified",
        source: str = "",
        actor: str = "studio-user",
    ) -> dict[str, Any]:
        project = self._project(project_id)
        self._ensure_active(project)
        if project.release is not None:
            raise WorkflowError("released project voiceover cannot be changed")
        clean_license = license.strip().lower()
        if clean_license not in {"unverified", "owned", "licensed", "commercial_use_allowed"}:
            raise WorkflowError("unsupported generated voiceover license")
        if clean_license != "unverified" and not source.strip():
            raise WorkflowError("voiceover license source or evidence is required")
        previous_audio = project.audio_track
        output_path = self.output_root / project_id / "audio" / f"voiceover-{uuid4().hex}.m4a"
        metadata = self.speech_synthesizer.synthesize(text, output_path, voice=voice, language=language)
        probe = probe_audio(output_path)
        if not probe.valid:
            output_path.unlink(missing_ok=True)
            raise WorkflowError("generated voiceover is not a readable audio file")
        if project.archived_at or project.release is not None or project.audio_track != previous_audio:
            output_path.unlink(missing_ok=True)
            raise WorkflowError("project audio changed while synthesis was running; retry on the current project")
        project.audio_track = str(output_path)
        project.dialogue_timeline = None
        project.audio_track_metadata = {
            "name": "voiceover.m4a",
            "uri": str(output_path),
            "format": probe.format,
            "duration_seconds": probe.duration_seconds,
            "license": clean_license,
            "license_source": source.strip(),
            "source": metadata["provider"],
            "voice": voice,
            "language": language,
            "text_length": len(text.strip()),
            "preview_only": bool(metadata.get("preview_only")),
            "size_bytes": output_path.stat().st_size,
            "sha256": sha256_file(output_path),
            "registered_at": datetime.now(timezone.utc).isoformat(),
        }
        self._invalidate_postproduction(project)
        self._record_event(project, action="voiceover.generated", actor=actor, message="Project voiceover generated.", details=copy.deepcopy(project.audio_track_metadata))
        self._persist()
        return self.project_view(project_id)

    @staticmethod
    def _dialogue_duration(project: ProjectRuntime) -> float:
        return sum(float(runtime.shot.duration_seconds) for runtime in project.shots.values())

    @staticmethod
    def _dialogue_windows(project: ProjectRuntime) -> dict[str, tuple[float, float]]:
        windows: dict[str, tuple[float, float]] = {}
        cursor = 0.0
        for runtime in project.shots.values():
            end = cursor + float(runtime.shot.duration_seconds)
            windows[runtime.shot.shot_id] = (cursor, end)
            cursor = end
        return windows

    def _validated_dialogue_lines(
        self, project: ProjectRuntime, lines: list[dict[str, Any] | DialogueLine]
    ) -> list[DialogueLine]:
        if not project.shots:
            raise WorkflowError("generate a shot plan before adding dialogue")
        if not lines or len(lines) > 80:
            raise WorkflowError("dialogue timeline must contain between 1 and 80 lines")
        windows = self._dialogue_windows(project)
        allowed_speakers = set(project.brief.characters) | {"旁白", "narrator", "Narrator"}
        parsed: list[DialogueLine] = []
        seen: set[str] = set()
        for raw in lines:
            try:
                line = raw if isinstance(raw, DialogueLine) else DialogueLine.model_validate(raw)
            except Exception as exc:
                raise WorkflowError("dialogue line does not match the timeline contract") from exc
            if line.line_id in seen:
                raise WorkflowError(f"duplicate dialogue line id: {line.line_id}")
            seen.add(line.line_id)
            if line.speaker not in allowed_speakers:
                raise WorkflowError(f"dialogue speaker is not declared: {line.speaker}")
            window = windows.get(line.shot_id)
            if window is None:
                raise WorkflowError(f"dialogue line references an unknown shot: {line.shot_id}")
            if line.end_seconds <= line.start_seconds:
                raise WorkflowError(f"dialogue line must end after it starts: {line.line_id}")
            if line.start_seconds < window[0] - 0.001 or line.end_seconds > window[1] + 0.001:
                raise WorkflowError(f"dialogue line is outside its shot window: {line.line_id}")
            parsed.append(line)
        return sorted(parsed, key=lambda line: (line.start_seconds, line.end_seconds, line.line_id))

    def _dialogue_fingerprint(self, project: ProjectRuntime, lines: list[DialogueLine]) -> str:
        return fingerprint({
            "characters": project.brief.characters,
            "shots": [
                {"shot_id": runtime.shot.shot_id, "duration_seconds": runtime.shot.duration_seconds}
                for runtime in project.shots.values()
            ],
            "lines": [line.model_dump(mode="json") for line in lines],
        })

    def _dialogue_timeline_current(self, project: ProjectRuntime) -> bool:
        timeline = project.dialogue_timeline
        if not timeline or not timeline.get("lines"):
            return False
        try:
            lines = self._validated_dialogue_lines(project, timeline["lines"])
        except WorkflowError:
            return False
        return timeline.get("fingerprint") == self._dialogue_fingerprint(project, lines)

    def generate_dialogue_timeline(
        self,
        project_id: str,
        *,
        lines: list[dict[str, Any] | DialogueLine],
        license: str = "unverified",
        source: str = "",
        actor: str = "studio-user",
    ) -> dict[str, Any]:
        """Synthesize one atomic multi-speaker track from timed dialogue lines."""
        project = self._project(project_id)
        self._ensure_active(project)
        if project.release is not None:
            raise WorkflowError("released project dialogue cannot be changed")
        clean_license = license.strip().lower()
        if clean_license not in {"unverified", "owned", "licensed", "commercial_use_allowed"}:
            raise WorkflowError("unsupported dialogue license")
        if clean_license != "unverified" and not source.strip():
            raise WorkflowError("dialogue license source or evidence is required")
        parsed = self._validated_dialogue_lines(project, lines)
        timeline_duration = self._dialogue_duration(project)
        expected_fingerprint = self._dialogue_fingerprint(project, parsed)
        previous_audio = project.audio_track
        audio_dir = self.output_root / project_id / "audio"
        audio_dir.mkdir(parents=True, exist_ok=True)
        segment_meta: list[dict[str, Any]] = []
        try:
            with TemporaryDirectory(prefix=".dialogue-", dir=audio_dir) as temp_dir:
                temp_root = Path(temp_dir)
                segments: list[tuple[Path, float, float]] = []
                for index, line in enumerate(parsed):
                    segment_path = temp_root / f"line-{index:03d}.m4a"
                    metadata = self.speech_synthesizer.synthesize(
                        line.text, segment_path, voice=line.voice, language=line.language
                    )
                    probe = probe_audio(segment_path)
                    if not probe.valid:
                        raise SpeechSynthesisError("dialogue segment is not readable audio")
                    segments.append((segment_path, line.start_seconds, line.end_seconds))
                    segment_meta.append({
                        "line_id": line.line_id,
                        "speaker": line.speaker,
                        "voice": line.voice,
                        "language": line.language,
                        "source_duration_seconds": probe.duration_seconds,
                        "provider": metadata.get("provider"),
                        "preview_only": bool(metadata.get("preview_only")),
                    })
                candidate = temp_root / "dialogue.m4a"
                render_timed_audio(segments, candidate, duration_seconds=timeline_duration)
                candidate_probe = probe_audio(candidate)
                if not candidate_probe.valid:
                    raise SpeechSynthesisError("dialogue timeline is not readable audio")
                final_path = audio_dir / f"dialogue-{uuid4().hex}.m4a"
                candidate.replace(final_path)
        except (SpeechSynthesisError, OSError, RuntimeError, ValueError) as exc:
            raise SpeechSynthesisError("dialogue synthesis failed; previous audio was kept") from exc

        if (project.archived_at or project.release is not None or project.audio_track != previous_audio or
                self._dialogue_fingerprint(project, parsed) != expected_fingerprint):
            final_path.unlink(missing_ok=True)
            raise WorkflowError("project changed while dialogue was synthesized; retry on the current project")
        project.audio_track = str(final_path)
        project.dialogue_timeline = {
            "schema_version": "mediaforge-dialogue-timeline-v1",
            "fingerprint": expected_fingerprint,
            "duration_seconds": timeline_duration,
            "lines": [line.model_dump(mode="json") for line in parsed],
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }
        project.audio_track_metadata = {
            "name": "dialogue.m4a",
            "uri": str(final_path),
            "format": probe_audio(final_path).format,
            "duration_seconds": probe_audio(final_path).duration_seconds,
            "license": clean_license,
            "license_source": source.strip(),
            "source": "multi-speaker-timeline",
            "line_count": len(parsed),
            "speakers": sorted({line.speaker for line in parsed}),
            "segments": segment_meta,
            "preview_only": any(item["preview_only"] for item in segment_meta),
            "size_bytes": final_path.stat().st_size,
            "sha256": sha256_file(final_path),
            "registered_at": datetime.now(timezone.utc).isoformat(),
        }
        self._invalidate_postproduction(project)
        self._record_event(
            project,
            action="dialogue_timeline.generated",
            actor=actor,
            message="Multi-speaker dialogue timeline generated.",
            details={
                "line_count": len(parsed),
                "speakers": sorted({line.speaker for line in parsed}),
                "timeline_duration_seconds": timeline_duration,
                "preview_only": project.audio_track_metadata["preview_only"],
            },
        )
        self._persist()
        return self.project_view(project_id)

    def generate_lipsync(
        self,
        project_id: str,
        *,
        actor: str = "studio-postproduction",
    ) -> dict[str, Any]:
        project = self._project(project_id)
        self._ensure_active(project)
        if project.release is not None:
            raise WorkflowError("released project lip-sync cannot be changed")
        if not project.final_mp4 or not Path(project.final_mp4).is_file():
            raise WorkflowError("export the project before running lip-sync")
        if not project.audio_track or not Path(project.audio_track).is_file():
            raise WorkflowError("configure an audio track before running lip-sync")
        adapter_status = self.lipsync_status()
        if not adapter_status.get("configured") or adapter_status.get("mode") == "disabled":
            raise WorkflowError("lip-sync adapter is not configured")
        previous_video = project.final_mp4
        previous_audio = project.audio_track
        output_dir = self.output_root / project_id / "lipsync"
        output_path = output_dir / f"final-lipsync-{uuid4().hex}.mp4"
        try:
            result = self.lipsync.render(
                Path(previous_video),
                Path(previous_audio),
                output_path,
                metadata={
                    "project_id": project_id,
                    "trace_id": project.trace_id,
                    "dialogue_timeline": copy.deepcopy(project.dialogue_timeline),
                },
            )
        except LipSyncError as exc:
            output_path.unlink(missing_ok=True)
            raise WorkflowError(str(exc)) from exc
        if project.release is not None or project.final_mp4 != previous_video or project.audio_track != previous_audio:
            output_path.unlink(missing_ok=True)
            raise WorkflowError("project changed while lip-sync was running; retry on the current project")
        project.final_mp4 = str(output_path)
        project.lipsync_artifact = {
            **result,
            "source_video": previous_video,
            "source_audio": previous_audio,
            "actor": actor,
        }
        project.status = ProjectStatus.EXPORTED
        project.delivery_package = None
        project.archive_package = None
        project.release = None
        self._record_event(
            project,
            action="postproduction.lipsync_completed",
            actor=actor,
            message="Lip-sync output generated and promoted to the project master.",
            details=copy.deepcopy(project.lipsync_artifact),
        )
        manifest_path = self.output_root / project_id / "project-manifest.json"
        if manifest_path.is_file():
            manifest_path.write_text(
                json.dumps(self.project_view(project_id), ensure_ascii=True, indent=2),
                encoding="utf-8",
            )
        self._persist()
        return self.project_view(project_id)

    def remove_audio_track(
        self,
        project_id: str,
        *,
        actor: str = "studio-user",
    ) -> dict[str, Any]:
        project = self._project(project_id)
        self._ensure_active(project)
        if project.release is not None:
            raise WorkflowError("released project audio cannot be changed")
        previous_audio = project.audio_track
        existing_audio = list(
            (self.output_root / project_id / "audio").glob("soundtrack.*")
        )
        if not previous_audio and not existing_audio:
            return self.project_view(project_id)
        if previous_audio:
            Path(previous_audio).unlink(missing_ok=True)
        for existing in existing_audio:
            existing.unlink(missing_ok=True)
        project.audio_track = None
        project.audio_track_metadata = None
        project.dialogue_timeline = None
        self._invalidate_postproduction(project)
        self._record_event(
            project,
            action="audio_track.removed",
            actor=actor,
            message="Project soundtrack removed.",
            details={"previous_audio_track": previous_audio},
        )
        self._persist()
        return self.project_view(project_id)

    def list_projects(
        self,
        *,
        include_archived: bool = False,
        tenant_id: str | None = None,
    ) -> dict[str, Any]:
        projects = sorted(
            [
                project
                for project in self.projects.values()
                if (include_archived or project.archived_at is None)
                and (
                    tenant_id is None
                    or project.brief.tenant_id == tenant_id
                )
            ],
            key=lambda project: project.created_at,
            reverse=True,
        )
        return {
            "count": len(projects),
            "include_archived": include_archived,
            "tenant_id": tenant_id,
            "projects": [self.project_summary(project) for project in projects],
        }

    def studio_overview(
        self,
        *,
        include_archived: bool = False,
        tenant_id: str | None = None,
    ) -> dict[str, Any]:
        projects = [
            project
            for project in self.projects.values()
            if (include_archived or project.archived_at is None)
            and (tenant_id is None or project.brief.tenant_id == tenant_id)
        ]
        job_counts = {status.value: 0 for status in JobStatus}
        for job in self.jobs.all():
            if any(project.brief.project_id == job.spec.project_id for project in projects):
                job_counts[job.status.value] += 1

        return {
            "project_count": len(projects),
            "active_projects": sum(1 for project in projects if project.archived_at is None),
            "archived_projects": sum(1 for project in projects if project.archived_at is not None),
            "planned_projects": sum(1 for project in projects if project.status == ProjectStatus.PLANNED),
            "in_progress_projects": sum(1 for project in projects if project.status == ProjectStatus.IN_PROGRESS),
            "exported_projects": sum(1 for project in projects if project.status == ProjectStatus.EXPORTED),
            "tenant_id": tenant_id,
            "released_projects": sum(1 for project in projects if project.release),
            "delivered_projects": sum(1 for project in projects if project.deliveries),
            "evaluated_projects": sum(1 for project in projects if project.evaluations),
            "average_evaluation_score": round(
                (
                    sum(project.evaluations[-1]["score"] for project in projects if project.evaluations)
                    / sum(1 for project in projects if project.evaluations)
                )
                if any(project.evaluations for project in projects)
                else 0.0,
                4,
            ),
            "total_spent": round(
                sum(self._cost_report(project)["spent"] for project in projects),
                4,
            ),
            "policy_warnings": sum(
                int(report.get("warnings") or 0)
                for project in projects
                for report in project.policy_reports
            ),
            "policy_blocks": sum(
                1
                for project in projects
                for report in project.policy_reports
                if report.get("blocked")
            ),
            "job_counts": job_counts,
            "recent_projects": [
                self.project_summary(project)
                for project in sorted(
                    projects,
                    key=lambda item: item.created_at,
                    reverse=True,
                )[:5]
            ],
        }

    def studio_metrics(
        self,
        *,
        include_archived: bool = False,
        tenant_id: str | None = None,
    ) -> dict[str, Any]:
        """Return deterministic studio-level throughput, quality, and cost metrics."""
        projects = [
            project
            for project in self.projects.values()
            if (include_archived or project.archived_at is None)
            and (tenant_id is None or project.brief.tenant_id == tenant_id)
        ]
        project_ids = {project.brief.project_id for project in projects}
        jobs = [
            job
            for job in self.jobs.all()
            if job.spec.project_id in project_ids
        ]
        shots = [
            runtime
            for project in projects
            for runtime in project.shots.values()
        ]
        job_statuses = {status.value: 0 for status in JobStatus}
        for job in jobs:
            job_statuses[job.status.value] += 1

        terminal_statuses = {
            JobStatus.SUCCEEDED,
            JobStatus.QUALITY_REJECTED,
            JobStatus.FAILED,
            JobStatus.CANCELED,
        }
        run_durations: list[float] = []
        queue_waits: list[float] = []
        for job in jobs:
            started_at = next(
                (
                    event.occurred_at
                    for event in job.events
                    if event.status == JobStatus.RUNNING
                ),
                None,
            )
            finished_at = next(
                (
                    event.occurred_at
                    for event in reversed(job.events)
                    if event.status in terminal_statuses
                ),
                None,
            )
            queued_at = next(
                (
                    event.occurred_at
                    for event in job.events
                    if event.status in {JobStatus.VALIDATED, JobStatus.QUEUED}
                ),
                None,
            )
            if started_at and finished_at and finished_at >= started_at:
                run_durations.append((finished_at - started_at).total_seconds())
            if queued_at and started_at and started_at >= queued_at:
                queue_waits.append((started_at - queued_at).total_seconds())

        approved_shots = sum(
            1 for runtime in shots if runtime.review_status == ReviewStatus.APPROVED
        )
        generated_shots = sum(1 for runtime in shots if runtime.current_artifact)
        quality_passed_shots = sum(
            1
            for runtime in shots
            if runtime.quality and runtime.quality.get("passed")
        )
        revisions = sum(runtime.revision for runtime in shots)
        budgets = sum(project.brief.budget for project in projects)
        spent = sum(self._cost_report(project)["spent"] for project in projects)
        terminal_jobs = sum(
            count for status, count in job_statuses.items()
            if status in {item.value for item in terminal_statuses}
        )
        continuity_reports = [
            self.project_continuity(project.brief.project_id)
            for project in projects
        ]
        compliance_reports = [
            self.project_compliance(project.brief.project_id)
            for project in projects
        ]
        deliveries = [
            delivery
            for project in projects
            for delivery in project.deliveries
        ]
        accepted_deliveries = sum(
            1 for delivery in deliveries if self._delivery_status(delivery) == "ACCEPTED"
        )
        provider_counts: dict[str, int] = {}
        for runtime in shots:
            provider = str((runtime.route or {}).get("provider") or "unrouted")
            provider_counts[provider] = provider_counts.get(provider, 0) + 1

        return {
            "schema_version": "mediaforge-studio-metrics-v1",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "include_archived": include_archived,
            "projects": {
                "total": len(projects),
                "active": sum(1 for project in projects if project.archived_at is None),
                "released": sum(1 for project in projects if project.release),
                "delivered": sum(1 for project in projects if project.deliveries),
                "archived": sum(1 for project in projects if project.archived_at is not None),
            },
            "shots": {
                "planned": len(shots),
                "generated": generated_shots,
                "approved": approved_shots,
                "quality_passed": quality_passed_shots,
                "revisions": revisions,
                "approval_rate": round(approved_shots / len(shots), 4) if shots else 0.0,
                "quality_pass_rate": round(quality_passed_shots / generated_shots, 4)
                if generated_shots
                else 0.0,
            },
            "jobs": {
                "total": len(jobs),
                "status_counts": job_statuses,
                "terminal": terminal_jobs,
                "success_rate": round(
                    job_statuses[JobStatus.SUCCEEDED.value] / terminal_jobs,
                    4,
                ) if terminal_jobs else 0.0,
                "retry_rate": round(
                    sum(1 for job in jobs if job.attempts > 1) / len(jobs),
                    4,
                ) if jobs else 0.0,
                "queue_depth": sum(
                    job_statuses[status.value]
                    for status in {
                        JobStatus.VALIDATED,
                        JobStatus.QUEUED,
                        JobStatus.ADMITTED,
                    }
                ),
                "average_run_seconds": round(
                    sum(run_durations) / len(run_durations), 4
                ) if run_durations else 0.0,
                "average_queue_wait_seconds": round(
                    sum(queue_waits) / len(queue_waits), 4
                ) if queue_waits else 0.0,
            },
            "cost": {
                "budget": round(budgets, 4),
                "spent": round(spent, 4),
                "remaining": round(budgets - spent, 4),
                "spent_ratio": round(spent / budgets, 4) if budgets else 0.0,
                "cost_per_approved_shot": round(spent / approved_shots, 4)
                if approved_shots
                else 0.0,
            },
            "quality": {
                "policy_blocks": sum(
                    report.get("blocks", 0)
                    for project in projects
                    for report in project.policy_reports
                ),
                "compliance_failures": sum(
                    report["summary"]["blocking_failures"]
                    for report in compliance_reports
                ),
                "continuity_passed_projects": sum(
                    1 for report in continuity_reports if report["passed"]
                ),
                "continuity_failed_projects": sum(
                    1 for report in continuity_reports if not report["passed"]
                ),
                "evaluation_passed_projects": sum(
                    1
                    for project in projects
                    if project.evaluations and project.evaluations[-1]["passed"]
                ),
            },
            "delivery": {
                "package_count": sum(1 for project in projects if project.delivery_package),
                "verified_package_count": sum(
                    1
                    for project in projects
                    if self._delivery_verification_state(project)[0]
                ),
                "delivery_count": len(deliveries),
                "accepted_count": accepted_deliveries,
                "pending_count": sum(
                    1 for delivery in deliveries if self._delivery_status(delivery) == "DELIVERED"
                ),
            },
            "providers": {
                "shot_route_counts": provider_counts,
                "configured_provider": self.provider_status.get("provider"),
                "provider_mode": self.provider_status.get("mode"),
                "tenant_id": tenant_id,
            },
        }

    def export_studio_metrics(
        self,
        *,
        file_format: str = "json",
        include_archived: bool = False,
        tenant_id: str | None = None,
    ) -> Path:
        """Persist a flattened studio metrics snapshot for operational review."""
        if file_format not in {"json", "csv"}:
            raise WorkflowError("studio metrics format must be json or csv")

        report = self.studio_metrics(
            include_archived=include_archived,
            tenant_id=tenant_id,
        )
        output_dir = self.output_root / "studio"
        output_dir.mkdir(parents=True, exist_ok=True)
        scope = "all" if include_archived else "active"
        path = output_dir / f"studio-metrics-{scope}.{file_format}"
        if file_format == "json":
            path.write_text(
                json.dumps(report, ensure_ascii=True, indent=2),
                encoding="utf-8",
            )
            return path

        rows: list[tuple[str, str, Any]] = []

        def flatten(section: str, key: str, value: Any) -> None:
            if isinstance(value, dict):
                for child_key, child_value in value.items():
                    flatten(section, f"{key}.{child_key}" if key else child_key, child_value)
                return
            rows.append((section, key, value))

        for section, values in report.items():
            if isinstance(values, dict):
                for key, value in values.items():
                    flatten(section, key, value)
            else:
                rows.append(("meta", section, values))
        with path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow(("section", "metric", "value"))
            writer.writerows(rows)
        return path

    def archive_project(
        self,
        project_id: str,
        *,
        actor: str = "studio-user",
    ) -> dict[str, Any]:
        project = self._project(project_id)
        if project.archived_at is None:
            project.archived_at = datetime.now(timezone.utc)
            self._record_event(
                project,
                action="project.archived",
                actor=actor,
                message="Project archived.",
            )
            self._persist()
        return self.project_view(project_id)

    def restore_project(
        self,
        project_id: str,
        *,
        actor: str = "studio-user",
    ) -> dict[str, Any]:
        project = self._project(project_id)
        if project.archived_at is not None:
            project.archived_at = None
            self._record_event(
                project,
                action="project.restored",
                actor=actor,
                message="Project restored.",
            )
            self._persist()
        return self.project_view(project_id)

    def clone_project(
        self,
        project_id: str,
        *,
        target_project_id: str,
        title_suffix: str = "Branch",
        actor: str = "studio-user",
        tenant_id: str | None = None,
    ) -> dict[str, Any]:
        source = self._project(project_id)
        if tenant_id and source.brief.tenant_id != tenant_id:
            raise TenantViolation("project does not belong to the authenticated tenant")
        if target_project_id in self.projects:
            raise WorkflowError(f"project already exists: {target_project_id}")

        suffix = title_suffix.strip()
        title = f"{source.brief.title} {suffix}".strip() if suffix else source.brief.title
        brief = source.brief.model_copy(
            update={
                "project_id": target_project_id,
                "title": title,
            }
        )
        self._enforce_project_quota(
            brief.tenant_id,
            additional_budget=float(brief.budget),
        )
        audio_track, audio_track_metadata = self._copy_audio_track(
            {
                "audio_track": source.audio_track,
                "audio_track_metadata": source.audio_track_metadata,
            },
            source_project_id=source.brief.project_id,
            target_project_id=target_project_id,
        )
        project = ProjectRuntime(
            brief=brief,
            prompt_versions=copy.deepcopy(source.prompt_versions),
            members=[
                {
                    "subject": actor.strip() or "studio-user",
                    "role": "owner",
                    "added_at": datetime.now(timezone.utc).isoformat(),
                }
            ],
            story_bible=self._clone_story_bible(source.story_bible, brief),
            source_documents=self._copy_source_documents(
                source.source_documents,
                source_project_id=source.brief.project_id,
                target_project_id=target_project_id,
            ),
            source_chapters=[
                NarrativeSourceChapter.model_validate(chapter.model_dump(mode="json"))
                for chapter in source.source_chapters
            ],
            narrative_event_candidates=[
                NarrativeEventCandidate.model_validate(candidate.model_dump(mode="json"))
                for candidate in source.narrative_event_candidates
            ],
            narrative_events=[
                NarrativeEvent.model_validate(event.model_dump(mode="json"))
                for event in source.narrative_events
            ],
            adaptation_scenes=[
                AdaptationScene.model_validate(scene.model_dump(mode="json"))
                for scene in source.adaptation_scenes
            ],
            status=ProjectStatus.PLANNED if source.shots else ProjectStatus.DRAFT,
            audio_track=audio_track,
            audio_track_metadata=audio_track_metadata,
            reference_assets=self._copy_reference_assets(
                source.reference_assets,
                source_project_id=source.brief.project_id,
                target_project_id=target_project_id,
            ),
        )
        for runtime in source.shots.values():
            cloned = self._runtime_template(runtime, target_project_id)
            project.shots[cloned.shot.shot_id] = cloned
        if source.dialogue_timeline:
            project.dialogue_timeline = copy.deepcopy(source.dialogue_timeline)
        self._refresh_reference_specs(project)
        policy_reports = self._policy_reports_for_template(project)
        blocked = [report for report in policy_reports if not report["passed"]]
        if blocked:
            raise PolicyViolation(
                "content policy blocked cloned project",
                {"reports": policy_reports, "blocked": blocked},
            )
        project.policy_reports.extend(policy_reports)

        self.projects[target_project_id] = project
        self._record_event(
            project,
            action="project.cloned",
            actor=actor,
            message=f"Project cloned from {project_id}.",
            details={
                "source_project_id": project_id,
                "shot_count": len(project.shots),
            },
        )
        self._persist()
        return self.project_view(target_project_id)

    def import_project_snapshot(
        self,
        snapshot: dict[str, Any],
        *,
        target_project_id: str | None = None,
        actor: str = "studio-user",
        tenant_id: str | None = None,
    ) -> dict[str, Any]:
        record = snapshot.get("project", snapshot)
        if not isinstance(record, dict) or "brief" not in record:
            raise WorkflowError("snapshot must contain a project record")
        source_brief = CreativeBrief.model_validate(record["brief"])
        if tenant_id and source_brief.tenant_id != tenant_id:
            raise TenantViolation("snapshot does not belong to the authenticated tenant")
        project_id = target_project_id or source_brief.project_id
        if project_id in self.projects:
            raise WorkflowError(f"project already exists: {project_id}")

        brief = source_brief.model_copy(update={"project_id": project_id})
        self._enforce_project_quota(
            brief.tenant_id,
            additional_budget=float(brief.budget),
        )
        audio_track, audio_track_metadata = self._copy_audio_track(
            record,
            source_project_id=source_brief.project_id,
            target_project_id=project_id,
        )
        project = ProjectRuntime(
            brief=brief,
            prompt_versions=copy.deepcopy(
                record.get("prompt_versions") or default_prompt_versions()
            ),
            evaluation_annotations=copy.deepcopy(
                record.get("evaluation_annotations", [])
            ),
            experiments=copy.deepcopy(record.get("experiments", [])),
            evaluation_baselines=copy.deepcopy(record.get("evaluation_baselines", [])),
            delivery_feedback=copy.deepcopy(record.get("delivery_feedback", [])),
            members=[
                {
                    "subject": actor.strip() or "studio-user",
                    "role": "owner",
                    "added_at": datetime.now(timezone.utc).isoformat(),
                }
            ],
            story_bible=self._clone_story_bible(record.get("story_bible"), brief),
            source_documents=self._copy_source_documents(
                record.get("source_documents", []),
                source_project_id=source_brief.project_id,
                target_project_id=project_id,
                source_document_blobs=snapshot.get("source_document_blobs"),
            ),
            source_chapters=[
                NarrativeSourceChapter.model_validate(chapter)
                for chapter in record.get("source_chapters", [])
            ],
            narrative_event_candidates=[
                NarrativeEventCandidate.model_validate(candidate)
                for candidate in record.get("narrative_event_candidates", [])
            ],
            narrative_events=[
                NarrativeEvent.model_validate(event)
                for event in record.get("narrative_events", [])
            ],
            adaptation_scenes=[
                AdaptationScene.model_validate(scene)
                for scene in record.get("adaptation_scenes", [])
            ],
            status=ProjectStatus.PLANNED if record.get("shots") else ProjectStatus.DRAFT,
            audio_track=audio_track,
            audio_track_metadata=audio_track_metadata,
            reference_assets=self._copy_reference_assets(
                record.get("reference_assets", []),
                source_project_id=source_brief.project_id,
                target_project_id=project_id,
            ),
            collaboration_documents=copy.deepcopy(
                record.get("collaboration_documents", {})
            ),
        )
        for shot_record in record.get("shots", []):
            runtime = ShotRuntime(
                shot=ShotCard.model_validate(
                    {
                        **shot_record["shot"],
                        "project_id": project_id,
                    }
                ),
                spec=GenerationSpec.model_validate(
                    {
                        **shot_record["spec"],
                        "project_id": project_id,
                    }
                ),
            )
            project.shots[runtime.shot.shot_id] = runtime
        if record.get("dialogue_timeline"):
            project.dialogue_timeline = copy.deepcopy(record["dialogue_timeline"])
        if record.get("edit_timeline"):
            project.edit_timeline = copy.deepcopy(record["edit_timeline"])
        self._refresh_reference_specs(project)
        policy_reports = self._policy_reports_for_template(project)
        blocked = [report for report in policy_reports if not report["passed"]]
        if blocked:
            raise PolicyViolation(
                "content policy blocked imported project",
                {"reports": policy_reports, "blocked": blocked},
            )
        project.policy_reports.extend(policy_reports)

        self.projects[project_id] = project
        self._record_event(
            project,
            action="project.imported",
            actor=actor,
            message="Project imported from portable snapshot.",
            details={
                "source_project_id": source_brief.project_id,
                "shot_count": len(project.shots),
            },
        )
        self._persist()
        return self.project_view(project_id)

    def import_delivery_package(
        self,
        *,
        package_path: str | None = None,
        package_b64: str | None = None,
        target_project_id: str | None = None,
        actor: str = "studio-user",
        tenant_id: str | None = None,
    ) -> dict[str, Any]:
        if not package_path and not package_b64:
            raise WorkflowError("package_zip or package_zip_b64 is required")

        package_bytes: bytes
        if package_b64:
            try:
                package_bytes = base64.b64decode(package_b64, validate=True)
            except ValueError as exc:
                raise WorkflowError("package_zip_b64 is not valid base64") from exc
        else:
            source_file = Path(package_path or "")
            if not source_file.is_file():
                raise WorkflowError(f"delivery package not found: {source_file}")
            package_bytes = source_file.read_bytes()

        try:
            with zipfile.ZipFile(io.BytesIO(package_bytes)) as archive:
                names = set(archive.namelist())
                if "project-manifest.json" not in names:
                    raise WorkflowError(
                        "delivery package is missing project-manifest.json"
                    )
                manifest = json.loads(
                    archive.read("project-manifest.json").decode("utf-8")
                )
                manifest_tenant_id = str(
                    (manifest.get("brief") or {}).get("tenant_id") or "default"
                )
                if tenant_id and manifest_tenant_id != tenant_id:
                    raise TenantViolation(
                        "delivery package does not belong to the authenticated tenant"
                    )
                governance = {
                    name: json.loads(archive.read(name).decode("utf-8"))
                    for name in (
                        "audit-log.json",
                        "audit-integrity.json",
                        "audit-anchors.json",
                        "content-credentials.json",
                        "evaluation-report.json",
                        "llmops-report.json",
                        "provider-benchmark.json",
                        "policy-report.json",
                        "shot-comparisons.json",
                    )
                    if name in names
                }
                source_project_id = manifest.get("project_id") or manifest["brief"]["project_id"]
                project_id = target_project_id or source_project_id
                if project_id in self.projects:
                    raise WorkflowError(f"project already exists: {project_id}")
                self._enforce_project_quota(
                    manifest_tenant_id,
                    additional_budget=float(
                        (manifest.get("brief") or {}).get("budget") or 0.0
                    ),
                )
                verification = self._verify_delivery_archive(
                    archive,
                    expected_project_id=source_project_id,
                )
                if not verification["passed"]:
                    raise WorkflowError(
                        "delivery package verification failed: "
                        f"{self._verification_error_summary(verification)}"
                    )

                output_dir = self.output_root / project_id
                output_dir.mkdir(parents=True, exist_ok=True)
                resolved_output_dir = output_dir.resolve()
                imported_package = output_dir / f"{project_id}-imported-delivery.zip"
                imported_package.write_bytes(package_bytes)

                for member in archive.namelist():
                    if member.endswith("/"):
                        continue
                    target = (output_dir / member).resolve()
                    if resolved_output_dir not in target.parents and target != resolved_output_dir:
                        raise WorkflowError(
                            f"delivery package contains unsafe path: {member}"
                        )
                    archive.extract(member, output_dir)

        except zipfile.BadZipFile as exc:
            raise WorkflowError("delivery package is not a valid zip file") from exc
        except (UnicodeDecodeError, json.JSONDecodeError, KeyError, TypeError) as exc:
            raise WorkflowError(
                "delivery package contains invalid JSON metadata"
            ) from exc

        try:
            record = self._normalize_delivery_manifest(
                manifest,
                target_project_id=project_id,
                imported_package=str(imported_package),
                source_project_id=source_project_id,
                governance=governance,
            )
            project = self._project_from_record(record)
            verification_report = self._delivery_verification_report(project_id)
            verification["report_path"] = str(verification_report)
            verification_report.write_text(
                json.dumps(verification, ensure_ascii=True, indent=2),
                encoding="utf-8",
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise WorkflowError(
                "delivery package project-manifest.json is invalid"
            ) from exc
        self.projects[project_id] = project
        self._record_event(
            project,
            action="project.imported_package",
            actor=actor,
            message="Project imported from delivery package.",
            details={
                "source_project_id": source_project_id,
                "package_zip": str(imported_package),
                "verification_passed": verification["passed"],
                "verification_report": verification["report_path"],
                "source_audit": copy.deepcopy(project.import_evidence),
            },
        )
        provenance_report = self.project_provenance(project_id)
        provenance_path = self._provenance_report(project_id)
        provenance_path.write_text(
            json.dumps(provenance_report, ensure_ascii=True, indent=2),
            encoding="utf-8",
        )
        compliance_report = self.project_compliance(project_id)
        compliance_path = self._compliance_report(project_id)
        compliance_path.write_text(
            json.dumps(compliance_report, ensure_ascii=True, indent=2),
            encoding="utf-8",
        )
        distribution_report = self.distribution_report(project_id)
        distribution_path = self._distribution_report(project_id)
        distribution_path.write_text(
            json.dumps(distribution_report, ensure_ascii=True, indent=2),
            encoding="utf-8",
        )
        self._persist()
        return self.project_view(project_id)

    def import_archive_package(
        self,
        *,
        archive_path: str | None = None,
        archive_b64: str | None = None,
        target_project_id: str | None = None,
        actor: str = "studio-user",
        tenant_id: str | None = None,
    ) -> dict[str, Any]:
        if not archive_path and not archive_b64:
            raise WorkflowError("archive_zip or archive_zip_b64 is required")

        archive_bytes: bytes
        if archive_b64:
            try:
                archive_bytes = base64.b64decode(archive_b64, validate=True)
            except ValueError as exc:
                raise WorkflowError("archive_zip_b64 is not valid base64") from exc
        else:
            source_file = Path(archive_path or "")
            if not source_file.is_file():
                raise WorkflowError(f"archive package not found: {source_file}")
            archive_bytes = source_file.read_bytes()

        try:
            with zipfile.ZipFile(io.BytesIO(archive_bytes)) as archive:
                verification = self._verify_manifest_archive(
                    archive,
                    summary_name="archive-summary.json",
                    schema_version="mediaforge-archive-verification-v1",
                )
                if not verification["passed"]:
                    raise WorkflowError(
                        "archive package verification failed: "
                        f"{self._verification_error_summary(verification)}"
                    )
                names = set(archive.namelist())
                nested_name = "deliverables/delivery-package.zip"
                if nested_name not in names:
                    raise WorkflowError(
                        "archive package is missing deliverables/delivery-package.zip"
                    )
                summary = json.loads(
                    archive.read("archive-summary.json").decode("utf-8")
                )
                source_project_id = (
                    verification.get("package_project_id")
                    or summary.get("project_id")
                )
                if not source_project_id:
                    raise WorkflowError(
                        "archive package summary is missing project_id"
                    )
                delivery_bytes = archive.read(nested_name)
        except zipfile.BadZipFile as exc:
            raise WorkflowError("archive package is not a valid zip file") from exc
        except (UnicodeDecodeError, json.JSONDecodeError, TypeError) as exc:
            raise WorkflowError(
                "archive package contains invalid JSON metadata"
            ) from exc

        project_id = target_project_id or f"{source_project_id}_imported"
        if project_id in self.projects:
            raise WorkflowError(f"project already exists: {project_id}")

        self.import_delivery_package(
            package_b64=base64.b64encode(delivery_bytes).decode("ascii"),
            target_project_id=project_id,
            actor=actor,
            tenant_id=tenant_id,
        )
        imported_archive = self._archive_package(project_id)
        imported_archive.write_bytes(archive_bytes)
        verification_report = self._archive_verification_report(project_id)
        verification = {
            **verification,
            "report_path": str(verification_report),
            "source_project_id": source_project_id,
            "target_project_id": project_id,
            "imported_from_archive": True,
        }
        verification_report.write_text(
            json.dumps(verification, ensure_ascii=True, indent=2),
            encoding="utf-8",
        )

        imported_runtime = self._project(project_id)
        imported_runtime.archive_package = str(imported_archive)
        self._record_event(
            imported_runtime,
            action="project.imported_archive",
            actor=actor,
            message="Project imported from verified final archive package.",
            details={
                "source_project_id": source_project_id,
                "target_project_id": project_id,
                "archive_package": str(imported_archive),
                "verification_report": str(verification_report),
                "verification_passed": verification["passed"],
            },
        )
        self._persist()
        project = self.project_view(project_id)
        return {
            **project,
            "archive_package": str(imported_archive),
            "archive_verified": True,
            "archive_verification_report": str(verification_report),
            "archive_import": {
                "source_project_id": source_project_id,
                "target_project_id": project_id,
                "verification": verification,
            },
        }

    def export_project_snapshot(
        self,
        project_id: str,
        *,
        actor: str = "studio-user",
    ) -> dict[str, Any]:
        project = self._project(project_id)
        output_dir = self.output_root / project_id
        output_dir.mkdir(parents=True, exist_ok=True)
        self._record_event(
            project,
            action="project.snapshot_exported",
            actor=actor,
            message="Portable project snapshot exported.",
        )
        jobs = [
            job.as_dict()
            for job in self.jobs.all()
            if job.spec.project_id == project_id
        ]
        payload = {
            "schema_version": "mediaforge-project-snapshot-v1",
            "exported_at": datetime.now(timezone.utc).isoformat(),
            "export_kind": "portable_project_snapshot",
            "project": self._project_record(project),
            "source_document_blobs": {
                document.document_id: base64.b64encode(
                    self._verified_source_document_path(project_id, document).read_bytes()
                ).decode("ascii")
                for document in project.source_documents
            },
            "jobs": jobs,
            "provider_status": self.provider_status,
            "note": (
                "Runtime artifact paths are preserved for provenance. Importing "
                "through the API resets generation state for a clean branch."
            ),
        }
        path = output_dir / "project-snapshot.json"
        path.write_text(
            json.dumps(payload, ensure_ascii=True, indent=2),
            encoding="utf-8",
        )
        self._persist()
        return {
            "project_id": project_id,
            "snapshot": str(path),
            "schema_version": payload["schema_version"],
            "shot_count": len(project.shots),
            "job_count": len(jobs),
        }

    def project_trace(self, project_id: str) -> dict[str, Any]:
        """Build a portable trace from persisted project, job, and asset history."""
        project = self._project(project_id)
        project_jobs = [
            job
            for job in self.jobs.all()
            if job.spec.project_id == project_id
        ]

        def as_datetime(value: Any) -> datetime | None:
            if isinstance(value, datetime):
                return value
            if not value:
                return None
            try:
                return datetime.fromisoformat(str(value))
            except ValueError:
                return None

        def span_id(kind: str, key: str) -> str:
            digest = hashlib.sha256(
                f"{project.trace_id}:{kind}:{key}".encode("utf-8")
            ).hexdigest()[:16]
            return f"span_{digest}"

        root_span_id = span_id("project", project_id)
        spans: list[dict[str, Any]] = [
            {
                "span_id": root_span_id,
                "parent_span_id": None,
                "trace_id": project.trace_id,
                "kind": "project",
                "name": "mediaforge.project.lifecycle",
                "status": "OK",
                "started_at": project.created_at.isoformat(),
                "ended_at": (
                    project.archived_at.isoformat()
                    if project.archived_at
                    else None
                ),
                "duration_ms": None,
                "actor": "system",
                "shot_id": None,
                "job_id": None,
                "artifact_id": None,
                "attributes": {
                    "project_status": project.status,
                    "archived": project.archived_at is not None,
                },
            }
        ]
        job_span_ids: dict[str, str] = {}
        timeline: list[datetime] = [project.created_at]

        for index, event in enumerate(project.audit_events):
            occurred_at = event.occurred_at
            timeline.append(occurred_at)
            event_span_id = span_id(
                "event",
                f"{index}:{event.action}:{occurred_at.isoformat()}",
            )
            status = (
                "ERROR"
                if "failed" in event.action or "blocked" in event.action
                else "OK"
            )
            spans.append(
                {
                    "span_id": event_span_id,
                    "parent_span_id": root_span_id,
                    "trace_id": event.trace_id or project.trace_id,
                    "kind": "audit",
                    "name": event.action,
                    "status": status,
                    "started_at": occurred_at.isoformat(),
                    "ended_at": occurred_at.isoformat(),
                    "duration_ms": 0.0,
                    "actor": event.actor,
                    "shot_id": event.shot_id,
                    "job_id": (event.details or {}).get("job_id"),
                    "artifact_id": (event.details or {}).get("artifact_id"),
                    "attributes": {
                        "message": event.message,
                        **(event.details or {}),
                    },
                }
            )

        for job in project_jobs:
            job_span_id = span_id("job", job.job_id)
            job_span_ids[job.job_id] = job_span_id
            job_times = [event.occurred_at for event in job.events]
            timeline.extend(job_times)
            started_at = min(job_times) if job_times else None
            ended_at = max(job_times) if job_times else None
            if job.status in {
                JobStatus.FAILED,
                JobStatus.QUALITY_REJECTED,
            }:
                span_status = "ERROR"
            elif job.status == JobStatus.CANCELED:
                span_status = "CANCELED"
            elif job.status == JobStatus.SUCCEEDED:
                span_status = "OK"
            else:
                span_status = "WAITING"
            spans.append(
                {
                    "span_id": job_span_id,
                    "parent_span_id": root_span_id,
                    "trace_id": job.trace_id or project.trace_id,
                    "kind": "job",
                    "name": "mediaforge.generation.job",
                    "status": span_status,
                    "started_at": started_at.isoformat() if started_at else None,
                    "ended_at": ended_at.isoformat() if ended_at else None,
                    "duration_ms": (
                        round((ended_at - started_at).total_seconds() * 1000, 2)
                        if started_at and ended_at
                        else None
                    ),
                    "actor": "generation-worker",
                    "shot_id": job.spec.shot_id,
                    "job_id": job.job_id,
                    "artifact_id": None,
                    "attributes": {
                        "idempotency_key": job.idempotency_key,
                        "attempts": job.attempts,
                        "max_attempts": job.max_attempts,
                        "last_error": job.last_error,
                        "retry_at": job.retry_at.isoformat() if job.retry_at else None,
                        "statuses": [event.status for event in job.events],
                        "artifact_ids": [artifact.artifact_id for artifact in job.artifacts],
                    },
                }
            )

        seen_artifacts: set[str] = set()
        for runtime in project.shots.values():
            entries = [
                *runtime.artifact_history,
                *runtime.variants,
            ]
            if runtime.current_artifact:
                entries.append(
                    {
                        "artifact": runtime.current_artifact,
                        "job_id": runtime.current_job_id,
                        "revision": runtime.revision,
                    }
                )
            for entry in entries:
                artifact = entry.get("artifact") if isinstance(entry, dict) else None
                if not isinstance(artifact, dict):
                    continue
                artifact_id = artifact.get("artifact_id")
                if not artifact_id or artifact_id in seen_artifacts:
                    continue
                seen_artifacts.add(artifact_id)
                created_at = as_datetime(artifact.get("created_at")) or as_datetime(
                    entry.get("created_at")
                )
                if created_at:
                    timeline.append(created_at)
                artifact_job_id = entry.get("job_id") or artifact.get("job_id")
                parent_span_id = job_span_ids.get(artifact_job_id, root_span_id)
                spans.append(
                    {
                        "span_id": span_id("artifact", artifact_id),
                        "parent_span_id": parent_span_id,
                        "trace_id": project.trace_id,
                        "kind": "artifact",
                        "name": "mediaforge.asset.created",
                        "status": "OK",
                        "started_at": created_at.isoformat() if created_at else None,
                        "ended_at": created_at.isoformat() if created_at else None,
                        "duration_ms": 0.0,
                        "actor": (entry.get("route") or {}).get("provider", "provider"),
                        "shot_id": runtime.shot.shot_id,
                        "job_id": artifact_job_id,
                        "artifact_id": artifact_id,
                        "attributes": {
                            "kind": artifact.get("kind"),
                            "uri": artifact.get("uri"),
                            "metadata_uri": artifact.get("metadata_uri"),
                            "sha256": artifact.get("sha256"),
                            "size_bytes": artifact.get("size_bytes"),
                            "revision": entry.get("revision", runtime.revision),
                            "variant_id": entry.get("variant_id"),
                            "current": artifact_id
                            == (runtime.current_artifact or {}).get("artifact_id"),
                            "quality_passed": bool(
                                (entry.get("quality") or {}).get("passed")
                            ),
                        },
                    }
                )

        spans.sort(key=lambda span: span.get("started_at") or "")
        if project.archived_at:
            timeline.append(project.archived_at)
        trace_end = max(timeline) if timeline else project.created_at
        root = next(span for span in spans if span["span_id"] == root_span_id)
        root["ended_at"] = trace_end.isoformat()
        root["duration_ms"] = round(
            max((trace_end - project.created_at).total_seconds(), 0.0) * 1000,
            2,
        )
        status_counts = {
            status.value: sum(1 for job in project_jobs if job.status == status)
            for status in JobStatus
        }
        return {
            "schema_version": "mediaforge-trace-v1",
            "project_id": project_id,
            "trace_id": project.trace_id,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "root_span": root,
            "summary": {
                "span_count": len(spans),
                "audit_event_count": len(project.audit_events),
                "content_credential_count": len(project.content_credentials),
                "job_count": len(project_jobs),
                "artifact_count": len(seen_artifacts),
                "job_statuses": status_counts,
                "failed_job_count": status_counts[JobStatus.FAILED.value]
                + status_counts[JobStatus.QUALITY_REJECTED.value],
                "estimated_cost": self._cost_report(project)["spent"],
                "started_at": project.created_at.isoformat(),
                "ended_at": trace_end.isoformat(),
                "duration_ms": root["duration_ms"],
            },
            "spans": spans,
        }

    def export_project_trace(
        self,
        project_id: str,
        *,
        actor: str = "studio-observer",
    ) -> dict[str, Any]:
        project = self._project(project_id)
        output_dir = self.output_root / project_id
        output_dir.mkdir(parents=True, exist_ok=True)
        self._record_event(
            project,
            action="project.trace_exported",
            actor=actor,
            message="Project trace exported.",
        )
        path = output_dir / "trace-report.json"
        report = self.project_trace(project_id)
        report["report_path"] = str(path)
        path.write_text(
            json.dumps(report, ensure_ascii=True, indent=2),
            encoding="utf-8",
        )
        self._persist()
        return {
            "project_id": project_id,
            "trace_id": project.trace_id,
            "trace_report": str(path),
            "span_count": report["summary"]["span_count"],
            "report": report,
        }

    def production_report(self, project_id: str) -> dict[str, Any]:
        project = self._project(project_id)
        jobs = self.project_jobs(project_id, limit=500)
        operations = self.operations_summary(project_id)
        evaluation = self.latest_evaluation_report(project_id)
        benchmark = self.latest_provider_benchmark(project_id)
        provider_contract = self.provider_contract_report(project_id)
        assets = self.asset_inventory(project_id)
        policy = self.policy_report(project_id)
        compliance = self.project_compliance(project_id)
        continuity = self.project_continuity(project_id)
        distribution = self.distribution_report(project_id)
        closeout_report = self._closeout_report(project_id)
        acceptance_report = self._acceptance_report(project_id)
        archive_verified, archive_verification_report = (
            self._archive_verification_state(project)
        )
        cost = self._cost_report(project)
        return {
            "schema_version": "mediaforge-production-report-v1",
            "project_id": project_id,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "provider": self.provider_status,
            "project": self.project_summary(project),
            "operations": operations,
            "cost": cost,
            "policy": policy,
            "compliance": compliance,
            "continuity": continuity,
            "distribution": distribution,
            "closeout": {
                "report_path": (
                    str(closeout_report) if closeout_report.is_file() else None
                ),
                "acceptance_report_path": (
                    str(acceptance_report)
                    if acceptance_report.is_file()
                    else None
                ),
                "archive_package": project.archive_package,
                "archive_verified": archive_verified,
                "archive_verification_report": (
                    str(archive_verification_report)
                    if archive_verified
                    else None
                ),
            },
            "assets": assets,
            "evaluation": evaluation,
            "benchmark": benchmark,
            "provider_contract": provider_contract,
            "routes": self.project_routes(project_id),
            "comparisons": self.project_comparisons(project_id),
            "jobs": jobs,
            "audit": {
                "count": len(project.audit_events),
                "latest_events": [
                    self._audit_event_view(event)
                    for event in project.audit_events[-20:]
                ],
            },
            "delivery": {
                "final_mp4": project.final_mp4,
                "delivery_package": project.delivery_package,
                "release": project.release,
                "count": len(project.deliveries),
                "pending_count": sum(
                    1
                    for delivery in project.deliveries
                    if self._delivery_status(delivery) == "DELIVERED"
                ),
                "accepted_count": sum(
                    1
                    for delivery in project.deliveries
                    if self._delivery_status(delivery) == "ACCEPTED"
                ),
                "rejected_count": sum(
                    1
                    for delivery in project.deliveries
                    if self._delivery_status(delivery) == "REJECTED"
                ),
            },
            "queue": self._queue_summary(project_id),
            "trace": self.project_trace(project_id),
        }

    def project_continuity(self, project_id: str) -> dict[str, Any]:
        """Run deterministic story/timeline continuity checks for a project."""
        project = self._project(project_id)
        shots = list(project.shots.values())
        brief_characters = set(project.brief.characters)
        timeline: list[dict[str, Any]] = []
        unknown_characters: list[dict[str, Any]] = []
        missing_descriptions: list[str] = []
        invalid_durations: list[dict[str, Any]] = []
        scene_counts: dict[str, int] = {}
        character_counts: dict[str, int] = {name: 0 for name in project.brief.characters}
        cursor = 0

        for index, runtime in enumerate(shots, start=1):
            shot = runtime.shot
            duration = int(shot.duration_seconds)
            start = cursor
            end = cursor + duration
            cursor = end
            scene = shot.scene.strip()
            scene_counts[scene] = scene_counts.get(scene, 0) + 1
            for character in shot.characters:
                if character in character_counts:
                    character_counts[character] += 1
                if character not in brief_characters:
                    unknown_characters.append(
                        {"shot_id": shot.shot_id, "character": character}
                    )
            if not shot.description.strip():
                missing_descriptions.append(shot.shot_id)
            if duration < 1 or duration > 5:
                invalid_durations.append(
                    {"shot_id": shot.shot_id, "duration_seconds": duration}
                )
            timeline.append(
                {
                    "index": index,
                    "shot_id": shot.shot_id,
                    "scene": scene,
                    "characters": list(shot.characters),
                    "start_seconds": start,
                    "end_seconds": end,
                    "duration_seconds": duration,
                    "subtitle": shot.subtitle_text or shot.description,
                }
            )

        sequence_numbers: list[int] = []
        for runtime in shots:
            try:
                sequence_numbers.append(int(runtime.shot.shot_id.rsplit("_", 1)[-1]))
            except (TypeError, ValueError):
                sequence_numbers.append(-1)
        expected_sequence = list(range(1, len(shots) + 1))
        scene_transitions = sum(
            1
            for previous, current in zip(shots, shots[1:])
            if previous.shot.scene != current.shot.scene
        )
        checks = [
            {
                "name": "shot_order",
                "label": "镜头顺序连续",
                "passed": sequence_numbers == expected_sequence,
                "severity": "error",
                "observed": sequence_numbers,
                "expected": expected_sequence,
            },
            {
                "name": "character_continuity",
                "label": "角色来自故事 Brief",
                "passed": bool(shots) and not unknown_characters and all(character_counts.values()),
                "severity": "error",
                "observed": {
                    "unknown_characters": unknown_characters,
                    "character_counts": character_counts,
                },
                "expected": "每个 Brief 角色至少出现在一个镜头，且镜头不包含未知角色",
            },
            {
                "name": "scene_continuity",
                "label": "场景字段完整",
                "passed": bool(shots) and all(item["scene"] for item in timeline),
                "severity": "error",
                "observed": {"scene_counts": scene_counts, "transitions": scene_transitions},
                "expected": "每个镜头都有场景，场景转场可追溯",
            },
            {
                "name": "timeline_continuity",
                "label": "时间轴连续",
                "passed": bool(shots) and not invalid_durations,
                "severity": "error",
                "observed": {
                    "duration_seconds": cursor,
                    "invalid_durations": invalid_durations,
                },
                "expected": "每个镜头时长在 1 至 5 秒之间，时间轴无空洞",
            },
            {
                "name": "target_duration",
                "label": "总时长匹配 Brief",
                "passed": bool(shots) and cursor == project.brief.duration_seconds,
                "severity": "error",
                "observed": cursor,
                "expected": project.brief.duration_seconds,
            },
            {
                "name": "subtitle_coverage",
                "label": "镜头描述可生成字幕",
                "passed": bool(shots) and not missing_descriptions,
                "severity": "error",
                "observed": {"missing_shot_ids": missing_descriptions},
                "expected": "每个镜头都有描述或字幕文本",
            },
        ]
        failed = [check for check in checks if not check["passed"]]
        return {
            "schema_version": "mediaforge-continuity-v1",
            "project_id": project_id,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "report_path": str(self._continuity_report(project_id)),
            "passed": not failed,
            "summary": {
                "shot_count": len(shots),
                "duration_seconds": cursor,
                "target_duration_seconds": project.brief.duration_seconds,
                "scene_count": len(scene_counts),
                "character_count": len(brief_characters),
                "transition_count": scene_transitions,
                "check_count": len(checks),
                "passed_checks": len(checks) - len(failed),
                "failed_count": len(failed),
            },
            "checks": checks,
            "timeline": timeline,
            "scene_counts": scene_counts,
            "character_counts": character_counts,
        }

    def export_project_continuity(
        self,
        project_id: str,
        *,
        actor: str = "continuity-agent",
    ) -> dict[str, Any]:
        project = self._project(project_id)
        self._ensure_active(project)
        output_dir = self.output_root / project_id
        output_dir.mkdir(parents=True, exist_ok=True)
        report = self.project_continuity(project_id)
        path = self._continuity_report(project_id)
        path.write_text(
            json.dumps(report, ensure_ascii=True, indent=2),
            encoding="utf-8",
        )
        self._record_event(
            project,
            action="project.continuity_exported",
            actor=actor,
            message="Project continuity report exported.",
            details={"continuity_report": str(path), "passed": report["passed"]},
        )
        self._persist()
        return {
            "project_id": project_id,
            "continuity_report": str(path),
            "report": report,
        }

    def export_production_report(
        self,
        project_id: str,
        *,
        actor: str = "studio-user",
    ) -> dict[str, Any]:
        project = self._project(project_id)
        output_dir = self.output_root / project_id
        output_dir.mkdir(parents=True, exist_ok=True)
        self._record_event(
            project,
            action="project.report_exported",
            actor=actor,
            message="Production operations report exported.",
        )
        path = output_dir / "production-report.json"
        report = self.production_report(project_id)
        report["report_path"] = str(path)
        path.write_text(
            json.dumps(report, ensure_ascii=True, indent=2),
            encoding="utf-8",
        )
        self._persist()
        return {
            "project_id": project_id,
            "production_report": str(path),
            "report": report,
        }

    def retrospective_report(self, project_id: str) -> dict[str, Any]:
        project = self._project(project_id)
        shots = list(project.shots.values())
        jobs = [
            job
            for job in self.jobs.all()
            if job.spec.project_id == project_id
        ]
        cost = self._cost_report(project)
        policy = self.policy_report(project_id)
        compliance = self.project_compliance(project_id)
        evaluation = self.latest_evaluation_report(project_id)
        benchmark = self.latest_provider_benchmark(project_id)
        distribution = self.distribution_report(project_id)
        delivery_verified, delivery_verification_report = (
            self._delivery_verification_state(project)
        )
        archive_verified, archive_verification_report = (
            self._archive_verification_state(project)
        )

        shot_count = len(shots)
        generated = sum(1 for runtime in shots if runtime.current_artifact)
        approved = sum(
            1
            for runtime in shots
            if runtime.review_status == ReviewStatus.APPROVED
        )
        quality_passed = sum(
            1
            for runtime in shots
            if runtime.quality and runtime.quality.get("passed")
        )
        revision_count = sum(runtime.revision for runtime in shots)
        variant_count = sum(len(runtime.variants) for runtime in shots)
        attempts = sum(
            len(runtime.artifact_history) + len(runtime.variants)
            for runtime in shots
        )
        job_statuses = {
            status.value: sum(1 for job in jobs if job.status == status)
            for status in JobStatus
        }
        accepted_deliveries = distribution["summary"]["accepted_count"]
        feedback_summary = self._delivery_feedback_summary(project.delivery_feedback)
        final_duration = (
            probe_video(Path(project.final_mp4)).duration_seconds
            if project.final_mp4 and Path(project.final_mp4).is_file()
            else None
        )

        milestones = []
        for action, label in [
            ("project.created", "Created"),
            ("project.planned", "Planned"),
            ("project.exported", "Exported"),
            ("project.packaged", "Packaged"),
            ("project.package_verified", "Verified"),
            ("project.released", "Released"),
            ("project.delivered", "Delivered"),
            ("project.delivery_accepted", "Accepted"),
            ("project.closed", "Closed"),
            ("project.archive_packaged", "Archive packaged"),
            ("project.archive_verified", "Archive verified"),
        ]:
            event = next(
                (
                    event
                    for event in project.audit_events
                    if event.action == action
                ),
                None,
            )
            if event:
                milestones.append(
                    {
                        "code": action,
                        "label": label,
                        "at": event.occurred_at.isoformat(),
                        "actor": event.actor,
                    }
                )

        lifecycle_end = project.archived_at or datetime.now(timezone.utc)
        elapsed_seconds = round(
            max((lifecycle_end - project.created_at).total_seconds(), 0.0),
            3,
        )
        maturity_checks = [
            shot_count > 0,
            shot_count > 0 and generated == shot_count,
            shot_count > 0 and approved == shot_count,
            shot_count > 0 and quality_passed == shot_count,
            bool(project.final_mp4 and Path(project.final_mp4).is_file()),
            bool(project.delivery_package and Path(project.delivery_package).is_file()),
            delivery_verified,
            compliance["passed"],
            project.release is not None,
            accepted_deliveries > 0,
            project.archived_at is not None,
            archive_verified,
        ]
        maturity_score = round(sum(maturity_checks) / len(maturity_checks), 4)

        insights: list[dict[str, Any]] = []

        def add_insight(
            area: str,
            severity: str,
            finding: str,
            recommendation: str,
        ) -> None:
            insights.append(
                {
                    "area": area,
                    "severity": severity,
                    "finding": finding,
                    "recommendation": recommendation,
                }
            )

        if not shot_count:
            add_insight(
                "planning",
                "warning",
                "No shot plan has been generated.",
                "Generate a structured shot plan before spending on media jobs.",
            )
        elif generated < shot_count:
            add_insight(
                "production",
                "warning",
                f"{shot_count - generated} shot(s) are still missing media.",
                "Finish queued generation work before release review.",
            )
        elif approved < shot_count:
            add_insight(
                "review",
                "warning",
                f"{shot_count - approved} generated shot(s) are not approved.",
                "Close review decisions or request targeted revisions.",
            )
        else:
            add_insight(
                "production",
                "positive",
                "All planned shots reached approval.",
                "Reuse the same plan-review-export cadence for the next episode.",
            )

        if revision_count > max(1, shot_count // 3):
            add_insight(
                "review",
                "warning",
                f"Revision volume reached {revision_count} across {shot_count} shot(s).",
                "Tighten prompts or style references before generation to reduce rework.",
            )
        if cost["spent_ratio"] is not None and cost["spent_ratio"] >= 0.9:
            add_insight(
                "cost",
                "warning",
                f"Spend used {cost['spent_ratio']:.0%} of the budget.",
                "Run provider benchmark before revisions and cap expensive variants.",
            )
        elif cost["spent_ratio"] is not None and cost["spent_ratio"] <= 0.5 and generated:
            add_insight(
                "cost",
                "positive",
                f"Spend used {cost['spent_ratio']:.0%} of the budget.",
                "There is budget headroom for optional A/B variants or polish passes.",
            )
        if evaluation["latest"] and not evaluation["latest"].get("passed"):
            add_insight(
                "quality",
                "warning",
                "Latest evaluation did not pass every release gate.",
                "Address failed evaluation gates before stakeholder delivery.",
            )
        elif evaluation["latest"]:
            add_insight(
                "quality",
                "positive",
                f"Latest evaluation scored {evaluation['latest']['score_percent']}/100.",
                "Preserve the evaluation gate as a required release step.",
            )
        if not compliance["passed"]:
            add_insight(
                "governance",
                "critical",
                "Compliance checks have blocking failures.",
                "Resolve compliance findings before release or archive packaging.",
            )
        elif delivery_verified and archive_verified:
            add_insight(
                "governance",
                "positive",
                "Delivery and archive packages are both verified.",
                "Use the archive package as the canonical handoff and recovery artifact.",
            )
        if project.release is None:
            add_insight(
                "delivery",
                "warning",
                "No release record exists yet.",
                "Release only after package verification and compliance pass.",
            )
        elif accepted_deliveries == 0:
            add_insight(
                "delivery",
                "warning",
                "Release exists but no accepted delivery is recorded.",
                "Capture delivery acknowledgement to close the business loop.",
            )
        elif project.archived_at is None:
            add_insight(
                "delivery",
                "warning",
                "Accepted delivery has not been closed out.",
                "Run closeout and archive packaging after recipient acceptance.",
            )
        if feedback_summary["blocking_open_count"]:
            add_insight(
                "delivery_feedback",
                "critical",
                f"{feedback_summary['blocking_open_count']} blocking delivery feedback item(s) remain open.",
                "Resolve blocker feedback and record the resolution before creating the next release branch.",
            )
        elif feedback_summary["unresolved_count"]:
            add_insight(
                "delivery_feedback",
                "warning",
                f"{feedback_summary['unresolved_count']} delivery feedback item(s) still need a disposition.",
                "Assign and triage delivery feedback so recipient input enters the next production cycle.",
            )

        return {
            "schema_version": "mediaforge-retrospective-v1",
            "project_id": project_id,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "report_path": str(self._retrospective_report(project_id)),
            "maturity_score": maturity_score,
            "maturity_percent": int(round(maturity_score * 100)),
            "summary": {
                "title": project.brief.title,
                "status": project.status,
                "archived": project.archived_at is not None,
                "closed": project.archived_at is not None
                and accepted_deliveries > 0,
                "sealed": archive_verified,
                "recommended_provider": (
                    benchmark["latest"].get("recommended_provider")
                    if benchmark.get("latest")
                    else None
                ),
                "evaluation_score_percent": (
                    evaluation["latest"].get("score_percent")
                    if evaluation.get("latest")
                    else None
                ),
            },
            "metrics": {
                "production": {
                    "shot_count": shot_count,
                    "generated_shots": generated,
                    "approved_shots": approved,
                    "approval_rate": round(approved / shot_count, 4)
                    if shot_count
                    else 0.0,
                    "quality_passed_shots": quality_passed,
                    "quality_pass_rate": round(quality_passed / shot_count, 4)
                    if shot_count
                    else 0.0,
                    "revision_count": revision_count,
                    "variant_count": variant_count,
                    "artifact_attempts": attempts,
                    "attempts_per_shot": round(attempts / shot_count, 4)
                    if shot_count
                    else 0.0,
                },
                "jobs": {
                    "total_count": len(jobs),
                    "statuses": job_statuses,
                    "retry_wait_jobs": job_statuses.get(JobStatus.RETRY_WAIT.value, 0),
                    "failed_jobs": job_statuses.get(JobStatus.FAILED.value, 0),
                    "quality_rejected_jobs": job_statuses.get(
                        JobStatus.QUALITY_REJECTED.value,
                        0,
                    ),
                    "canceled_jobs": job_statuses.get(JobStatus.CANCELED.value, 0),
                },
                "cost": {
                    **cost,
                    "cost_per_approved_shot": round(cost["spent"] / approved, 4)
                    if approved
                    else None,
                    "cost_per_second": round(cost["spent"] / final_duration, 4)
                    if final_duration
                    else None,
                },
                "delivery": {
                    "released": project.release is not None,
                    "delivery_count": len(project.deliveries),
                    "accepted_deliveries": accepted_deliveries,
                    "pending_deliveries": distribution["summary"]["pending_count"],
                    "rejected_deliveries": distribution["summary"]["rejected_count"],
                    "delivery_verified": delivery_verified,
                    "delivery_verification_report": (
                        str(delivery_verification_report)
                        if delivery_verified
                        else None
                    ),
                    "archive_verified": archive_verified,
                    "archive_verification_report": (
                        str(archive_verification_report)
                        if archive_verified
                        else None
                    ),
                    "feedback": feedback_summary,
                },
                "governance": {
                    "policy_passed": policy["passed"],
                    "policy_warnings": policy["warning_count"],
                    "policy_blocked": policy["blocked_count"],
                    "compliance_passed": compliance["passed"],
                    "compliance_blocking_failures": compliance["summary"][
                        "blocking_failures"
                    ],
                    "audit_event_count": len(project.audit_events),
                },
            },
            "timeline": {
                "created_at": project.created_at.isoformat(),
                "archived_at": (
                    project.archived_at.isoformat()
                    if project.archived_at
                    else None
                ),
                "elapsed_seconds": elapsed_seconds,
                "milestones": milestones,
            },
            "insights": insights,
            "next_actions": [
                insight["recommendation"]
                for insight in insights
                if insight["severity"] in {"critical", "warning"}
            ],
        }

    def export_retrospective_report(
        self,
        project_id: str,
        *,
        actor: str = "studio-ops",
    ) -> dict[str, Any]:
        project = self._project(project_id)
        output_dir = self.output_root / project_id
        output_dir.mkdir(parents=True, exist_ok=True)
        self._record_event(
            project,
            action="project.retrospective_exported",
            actor=actor,
            message="Project retrospective report exported.",
        )
        path = self._retrospective_report(project_id)
        report = self.retrospective_report(project_id)
        report["report_path"] = str(path)
        path.write_text(
            json.dumps(report, ensure_ascii=True, indent=2),
            encoding="utf-8",
        )
        self._persist()
        return {
            "project_id": project_id,
            "retrospective_report": str(path),
            "report": report,
        }

    def _training_dataset_path(self, project_id: str) -> Path:
        return self.output_root / project_id / "training-dataset.jsonl"

    def _training_dataset_manifest_path(self, project_id: str) -> Path:
        return self.output_root / project_id / "training-dataset-manifest.json"

    @staticmethod
    def _dataset_record_count(manifest_path: Path) -> int:
        if not manifest_path.is_file():
            return 0
        try:
            payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            return 0
        return int(payload.get("record_count") or 0)

    def training_dataset(self, project_id: str) -> dict[str, Any]:
        project = self._project(project_id)
        dataset_path = self._training_dataset_path(project_id)
        manifest_path = self._training_dataset_manifest_path(project_id)
        eligible = sum(
            1
            for runtime in project.shots.values()
            if runtime.review_status == ReviewStatus.APPROVED
            and runtime.current_artifact
            and runtime.quality
            and runtime.quality.get("passed")
        )
        return {
            "project_id": project_id,
            "schema_version": "mediaforge-training-dataset-v1",
            "ready": dataset_path.is_file() and manifest_path.is_file(),
            "eligible_record_count": eligible,
            "record_count": self._dataset_record_count(manifest_path),
            "delivery_feedback": self._delivery_feedback_summary(project.delivery_feedback),
            "dataset_path": str(dataset_path) if dataset_path.is_file() else None,
            "manifest_path": str(manifest_path) if manifest_path.is_file() else None,
        }

    def export_training_dataset(
        self,
        project_id: str,
        *,
        actor: str = "dataset-ops",
    ) -> dict[str, Any]:
        """Export reviewed production evidence as JSONL for evaluation or training."""
        project = self._project(project_id)
        records: list[dict[str, Any]] = []
        feedback_by_target: dict[tuple[str, str], list[dict[str, Any]]] = {}
        for feedback in project.delivery_feedback:
            target_key = (
                str(feedback.get("target_type") or "project"),
                str(feedback.get("target_id") or project_id),
            )
            feedback_by_target.setdefault(target_key, []).append(
                {
                    key: feedback.get(key)
                    for key in (
                        "feedback_id", "delivery_id", "category", "severity", "verdict",
                        "rating", "comment", "status", "resolution", "submitted_at", "resolved_at",
                    )
                }
            )
        project_split = "validation" if int(hashlib.sha256(project_id.encode("utf-8")).hexdigest()[:2], 16) < 51 else "train"
        for runtime in project.shots.values():
            artifact = runtime.current_artifact
            if runtime.review_status != ReviewStatus.APPROVED or not artifact:
                continue
            quality = runtime.quality or {}
            if not quality.get("passed"):
                continue
            artifact_path = Path(str(artifact.get("uri") or ""))
            if not artifact_path.is_file():
                continue
            records.append({
                "schema_version": "mediaforge-training-record-v1",
                "project_id": project_id,
                "tenant_id": project.brief.tenant_id,
                "split": project_split,
                "shot_id": runtime.shot.shot_id,
                "input": {
                    "scene": runtime.shot.scene,
                    "description": runtime.shot.description,
                    "characters": runtime.shot.characters,
                    "subtitle_text": runtime.shot.subtitle_text,
                    "intent": runtime.spec.intent.model_dump(mode="json"),
                    "workflow": runtime.spec.workflow.model_dump(mode="json"),
                    "reference_assets": [ref.model_dump(mode="json") for ref in runtime.spec.reference_assets],
                },
                "target": {
                    "artifact_id": artifact.get("artifact_id"),
                    "uri": artifact.get("uri"),
                    "sha256": artifact.get("sha256") or sha256_file(artifact_path),
                    "kind": artifact.get("kind"),
                    "duration_seconds": artifact.get("duration_seconds"),
                },
                "provider": copy.deepcopy(runtime.route or {}),
                "quality": copy.deepcopy(quality),
                "human_feedback": [
                    {
                        "status": review.status,
                        "comment": review.comment,
                        "actor": review.actor,
                        "occurred_at": review.occurred_at.isoformat(),
                    }
                    for review in runtime.reviews
                ],
                "stakeholder_feedback": [
                    *feedback_by_target.get(("project", project_id), []),
                    *feedback_by_target.get(("shot", runtime.shot.shot_id), []),
                ],
                "license_evidence": [
                    {"asset_id": ref.asset_id, "license": ref.license, "source": ref.source}
                    for ref in runtime.spec.reference_assets
                ],
            })
        dataset_path = self._training_dataset_path(project_id)
        manifest_path = self._training_dataset_manifest_path(project_id)
        dataset_path.parent.mkdir(parents=True, exist_ok=True)
        dataset_path.write_text("".join(json.dumps(record, ensure_ascii=True) + "\n" for record in records), encoding="utf-8")
        manifest = {
            "schema_version": "mediaforge-training-dataset-v1",
            "project_id": project_id,
            "tenant_id": project.brief.tenant_id,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "created_by": actor,
            "record_count": len(records),
            "approved_shot_count": len(records),
            "excluded_shot_count": len(project.shots) - len(records),
            "split": project_split,
            "source": "approved-current-artifacts",
            "dataset_path": str(dataset_path),
            "delivery_feedback": self._delivery_feedback_summary(project.delivery_feedback),
        }
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=True, indent=2), encoding="utf-8")
        self._record_event(
            project,
            action="dataset.exported",
            actor=actor,
            message="Reviewed production dataset exported.",
            details={"record_count": len(records), "dataset_path": str(dataset_path), "manifest_path": str(manifest_path)},
        )
        self._persist()
        return {**manifest, "manifest_path": str(manifest_path)}

    def project_provenance(self, project_id: str) -> dict[str, Any]:
        project = self._project(project_id)
        jobs = self.project_jobs(project_id, limit=500)
        assets = self.asset_inventory(project_id)
        policy = self.policy_report(project_id)
        compliance = self.project_compliance(project_id)
        distribution = self.distribution_report(project_id)
        evaluation = self.latest_evaluation_report(project_id)
        benchmark = self.latest_provider_benchmark(project_id)
        provider_contract = self.provider_contract_report(project_id)
        cost = self._cost_report(project)
        delivery_verified, verification_report = self._delivery_verification_state(
            project
        )
        closeout_report = self._closeout_report(project_id)
        acceptance_report = self._acceptance_report(project_id)
        verification = None
        if verification_report.is_file():
            try:
                verification = json.loads(
                    verification_report.read_text(encoding="utf-8")
                )
            except (OSError, UnicodeDecodeError, json.JSONDecodeError):
                verification = None

        job_by_id = {
            job["job_id"]: job
            for job in jobs["jobs"]
            if job.get("job_id")
        }
        shot_lineage = []
        artifact_count = 0
        revision_count = 0
        variant_count = 0
        for runtime in project.shots.values():
            artifact_lineage = []
            entries = [
                *runtime.artifact_history,
                *[
                    {
                        "revision": variant.get("revision", runtime.revision),
                        "job_id": variant.get("job_id"),
                        "variant_id": variant.get("variant_id"),
                        "artifact": variant.get("artifact"),
                        "route": variant.get("route"),
                        "quality": variant.get("quality"),
                        "candidate": True,
                        "promoted": bool(variant.get("promoted")),
                        "created_at": variant.get("created_at"),
                    }
                    for variant in runtime.variants
                ],
            ]
            seen_artifacts: set[str] = set()
            for entry in entries:
                artifact = entry.get("artifact")
                if not artifact:
                    continue
                artifact_id = artifact.get("artifact_id")
                if not artifact_id or artifact_id in seen_artifacts:
                    continue
                seen_artifacts.add(artifact_id)
                artifact_count += 1
                job_id = entry.get("job_id") or artifact.get("job_id")
                artifact_lineage.append(
                    {
                        "artifact_id": artifact_id,
                        "job_id": job_id,
                        "variant_id": entry.get("variant_id"),
                        "revision": entry.get("revision", runtime.revision),
                        "uri": artifact.get("uri"),
                        "metadata_uri": artifact.get("metadata_uri"),
                        "sha256": artifact.get("sha256"),
                        "size_bytes": artifact.get("size_bytes"),
                        "kind": artifact.get("kind"),
                        "provider": (entry.get("route") or {}).get("provider"),
                        "route": entry.get("route"),
                        "quality": entry.get("quality"),
                        "job_status": (
                            job_by_id.get(job_id, {}).get("status")
                            if job_id
                            else None
                        ),
                        "current": (
                            runtime.current_artifact is not None
                            and runtime.current_artifact.get("artifact_id")
                            == artifact_id
                        ),
                        "candidate": bool(entry.get("candidate")),
                        "promoted": bool(entry.get("promoted")),
                        "created_at": artifact.get("created_at")
                        or entry.get("created_at"),
                    }
                )
            revision_count += runtime.revision
            variant_count += len(runtime.variants)
            shot_lineage.append(
                {
                    "shot_id": runtime.shot.shot_id,
                    "scene": runtime.shot.scene,
                    "description": runtime.shot.description,
                    "characters": runtime.shot.characters,
                    "review_status": runtime.review_status,
                    "revision": runtime.revision,
                    "generation_spec": runtime.spec.model_dump(mode="json"),
                    "current_route": runtime.route,
                    "current_quality": runtime.quality,
                    "artifact_lineage": artifact_lineage,
                    "reviews": [
                        {
                            "status": review.status,
                            "comment": review.comment,
                            "actor": review.actor,
                            "occurred_at": review.occurred_at.isoformat(),
                        }
                        for review in runtime.reviews
                    ],
                }
            )

        return {
            "schema_version": "mediaforge-provenance-v1",
            "report_path": str(self._provenance_report(project_id)),
            "project": {
                "project_id": project.brief.project_id,
                "title": project.brief.title,
                "status": project.status,
                "created_at": project.created_at.isoformat(),
                "brief": project.brief.model_dump(mode="json"),
                "story_bible": project.story_bible,
            },
            "summary": {
                "shot_count": len(project.shots),
                "generated_shots": sum(
                    1
                    for runtime in project.shots.values()
                    if runtime.current_artifact
                ),
                "approved_shots": sum(
                    1
                    for runtime in project.shots.values()
                    if runtime.review_status == ReviewStatus.APPROVED
                ),
                "job_count": jobs["total_count"],
                "artifact_count": artifact_count,
                "revision_count": revision_count,
                "variant_count": variant_count,
                "audit_event_count": len(project.audit_events),
                "policy_report_count": len(project.policy_reports),
                "content_credential_count": len(project.content_credentials),
                "provider_contract_passed": provider_contract["summary"]["protocol_passed"],
                "delivery_verified": delivery_verified,
                "delivery_count": len(project.deliveries),
                "delivery_pending_count": sum(
                    1
                    for delivery in project.deliveries
                    if self._delivery_status(delivery) == "DELIVERED"
                ),
                "delivery_accepted_count": sum(
                    1
                    for delivery in project.deliveries
                    if self._delivery_status(delivery) == "ACCEPTED"
                ),
                "delivery_rejected_count": sum(
                    1
                    for delivery in project.deliveries
                    if self._delivery_status(delivery) == "REJECTED"
                ),
            },
            "lineage": {
                "shots": shot_lineage,
                "outputs": assets["outputs"],
            },
            "governance": {
                "policy": policy,
                "compliance": compliance,
                "distribution": distribution,
                "closeout": {
                    "report_path": (
                        str(closeout_report) if closeout_report.is_file() else None
                    ),
                    "acceptance_report_path": (
                        str(acceptance_report)
                        if acceptance_report.is_file()
                        else None
                    ),
                },
                "evaluation": evaluation,
                "benchmark": benchmark,
                "provider_contract": provider_contract,
                "delivery_verification": verification,
                "release": project.release,
                "content_credentials": self.project_content_credentials(project_id),
            },
            "cost": cost,
            "provider": self.provider_status,
            "audit": {
                "count": len(project.audit_events),
                "events": [
                    self._audit_event_view(event)
                    for event in project.audit_events
                ],
                "integrity": self.audit_integrity_report(project_id),
                "import_evidence": copy.deepcopy(project.import_evidence),
            },
        }

    def export_project_provenance(
        self,
        project_id: str,
        *,
        actor: str = "studio-governance",
    ) -> dict[str, Any]:
        project = self._project(project_id)
        self._ensure_active(project)
        output_dir = self.output_root / project_id
        output_dir.mkdir(parents=True, exist_ok=True)
        self._record_event(
            project,
            action="project.provenance_exported",
            actor=actor,
            message="Project provenance report exported.",
        )
        path = self._provenance_report(project_id)
        report = self.project_provenance(project_id)
        path.write_text(
            json.dumps(report, ensure_ascii=True, indent=2),
            encoding="utf-8",
        )
        self._persist()
        return {
            "project_id": project_id,
            "provenance_report": str(path),
            "report": report,
        }

    @staticmethod
    def _asset_rights_records_required() -> bool:
        return os.getenv("MEDIAFORGE_REQUIRE_ASSET_RIGHTS_RECORD", "false").strip().lower() in {
            "1",
            "true",
            "yes",
            "on",
        }

    def project_compliance(self, project_id: str) -> dict[str, Any]:
        project = self._project(project_id)
        assets = self.asset_inventory(project_id)
        policy = self.policy_report(project_id)
        checked_at = datetime.now(timezone.utc).isoformat()
        checks: list[dict[str, Any]] = []

        def add_check(
            name: str,
            label: str,
            passed: bool,
            observed: Any,
            expected: Any,
            *,
            blocking: bool = True,
        ) -> None:
            checks.append(
                {
                    "name": name,
                    "label": label,
                    "passed": bool(passed),
                    "blocking": blocking,
                    "observed": observed,
                    "expected": expected,
                }
            )

        allowed_reference_licenses = {
            "cc0",
            "commercial_use_allowed",
            "licensed",
            "mock_generated",
            "owned",
            "public_domain",
            "user_supplied_or_project_owned",
        }
        reference_assets = assets["reference_assets"]
        blocked_references = [
            {
                "asset_id": asset["asset_id"],
                "name": asset.get("name"),
                "license": asset.get("license"),
            }
            for asset in reference_assets
            if str(asset.get("license") or "").strip().lower()
            not in allowed_reference_licenses
        ]
        reference_licenses = sorted(
            {
                str(asset.get("license") or "").strip().lower()
                for asset in reference_assets
            }
        )
        add_check(
            "reference_asset_licenses",
            "Reference assets have approved licenses",
            not blocked_references,
            blocked_references or reference_licenses,
            sorted(allowed_reference_licenses),
        )

        audio_metadata = project.audio_track_metadata or {}
        audio_license = str(audio_metadata.get("license") or "").strip().lower()
        audio_source = str(audio_metadata.get("source") or "").strip()
        audio_license_passed = (
            project.audio_track is None
            or (
                bool(audio_metadata)
                and audio_license in allowed_reference_licenses
                and bool(audio_source)
            )
        )
        add_check(
            "audio_track_license",
            "Audio track has an approved license and source",
            audio_license_passed,
            {
                "configured": project.audio_track is not None,
                "license": audio_license or None,
                "source": audio_source or None,
            },
            {
                "licenses": sorted(allowed_reference_licenses),
                "source": "required when audio is configured",
            },
        )
        add_check(
            "audio_track_production",
            "Audio track is not a deterministic preview tone",
            project.audio_track is None or not audio_metadata.get("preview_only"),
            bool(audio_metadata.get("preview_only")),
            False,
        )

        generated_assets = assets["generated_assets"]
        missing_hashes = [
            asset["asset_id"]
            for asset in generated_assets
            if not str(asset.get("sha256") or "").strip()
        ]
        add_check(
            "artifact_hashes",
            "Generated assets have immutable hashes",
            not missing_hashes,
            missing_hashes,
            "sha256 present for every generated asset",
        )

        missing_files = [
            {
                "asset_id": asset["asset_id"],
                "uri": asset.get("uri"),
            }
            for asset in generated_assets
            if not Path(str(asset.get("uri") or "")).is_file()
        ]
        add_check(
            "artifact_files",
            "Generated asset files are present",
            not missing_files,
            missing_files,
            "all generated asset files exist on disk",
        )

        missing_metadata = []
        for asset in generated_assets:
            issue = self._artifact_metadata_issue(asset)
            if issue:
                missing_metadata.append(
                    {
                        "asset_id": asset["asset_id"],
                        "metadata_uri": asset.get("metadata_uri"),
                        "issue": issue,
                    }
                )
        add_check(
            "artifact_metadata",
            "Generated assets retain execution metadata",
            not missing_metadata,
            missing_metadata,
            "readable metadata sidecar for every generated asset",
        )

        missing_provider = [
            asset["asset_id"]
            for asset in generated_assets
            if not str(asset.get("provider") or "").strip()
        ]
        add_check(
            "generated_asset_providers",
            "Generated assets retain provider attribution",
            not missing_provider,
            missing_provider,
            "provider present for every generated asset",
        )

        configured = bool(self.provider_status.get("configured", False))
        provider_name = self.provider_status.get("provider") or self.provider.name
        provider_mode = self.provider_status.get("mode")
        add_check(
            "provider_configuration",
            "Generation provider is configured",
            configured and bool(provider_name),
            {
                "configured": configured,
                "mode": provider_mode,
                "provider": provider_name,
            },
            "configured provider with a named mode",
        )

        allowed_template_roots = self._approved_workflow_template_roots()
        workflow_templates = sorted(
            {
                runtime.spec.workflow.template_id
                for runtime in project.shots.values()
            }
        )
        blocked_templates = [
            template_id
            for template_id in workflow_templates
            if template_id.split(":", 1)[0] not in allowed_template_roots
        ]
        add_check(
            "workflow_allowlist",
            "Workflow templates are approved",
            not blocked_templates,
            blocked_templates or workflow_templates,
            sorted(allowed_template_roots),
        )

        allowed_lora_prefixes = ("cinematic_style:", "mock_lora:")
        lora_ids = sorted(
            {
                lora_id
                for runtime in project.shots.values()
                for lora_id in runtime.spec.workflow.allowed_lora_ids
            }
        )
        blocked_loras = [
            lora_id
            for lora_id in lora_ids
            if not str(lora_id).startswith(allowed_lora_prefixes)
        ]
        add_check(
            "lora_allowlist",
            "LoRA adapters are approved",
            not blocked_loras,
            blocked_loras or lora_ids,
            list(allowed_lora_prefixes),
        )

        registry_requirements: list[tuple[str, str]] = [
            ("license", license_name)
            for license_name in reference_licenses
        ]
        if audio_license and audio_license not in reference_licenses:
            registry_requirements.append(("license", audio_license))
        if provider_name:
            registry_requirements.append(("provider", str(provider_name)))
        registry_requirements.extend(
            ("workflow", template_id.split(":", 1)[0])
            for template_id in workflow_templates
        )
        registry_requirements.extend(("lora", lora_id) for lora_id in lora_ids)
        registry_assessment = self.license_registry.assess(registry_requirements)
        add_check(
            "license_registry",
            "Project dependencies are registered",
            registry_assessment["passed"],
            registry_assessment["unregistered"],
            "all licenses, providers, workflows, and LoRA identifiers are registered",
        )

        rights_records = [
            {
                "asset_id": asset.get("asset_id"),
                "sha256": str(asset.get("sha256") or "").lower(),
                "kind": "reference_asset",
            }
            for asset in reference_assets
            if asset.get("uri") or asset.get("sha256")
        ] + [
            {
                "asset_id": document.document_id,
                "sha256": str(document.sha256 or "").lower(),
                "kind": "source_document",
            }
            for document in project.source_documents
        ]
        rights_requirements = [
            ("asset", record["sha256"])
            for record in rights_records
            if record["sha256"]
        ]
        rights_assessment = self.license_registry.assess(rights_requirements)
        missing_rights_hashes = [
            record for record in rights_records if not record["sha256"]
        ]
        asset_rights_enforced = self._asset_rights_records_required()
        add_check(
            "asset_rights_registry",
            "Asset-specific rights records are active",
            not missing_rights_hashes and rights_assessment["passed"],
            {
                "enforced": asset_rights_enforced,
                "missing_hashes": missing_rights_hashes,
                "unregistered": rights_assessment["unregistered"],
            },
            "active registry entries for every reference and source asset SHA-256",
            blocking=asset_rights_enforced,
        )

        add_check(
            "safety_policy",
            "Safety policy gate passed",
            bool(policy["passed"]),
            {
                "blocked_count": policy["blocked_count"],
                "warning_count": policy["warning_count"],
                "reports": policy["count"],
            },
            "policy report passed",
        )

        blocking_failures = [
            check for check in checks if check["blocking"] and not check["passed"]
        ]
        return {
            "schema_version": "mediaforge-compliance-v1",
            "project_id": project_id,
            "checked_at": checked_at,
            "report_path": str(self._compliance_report(project_id)),
            "passed": not blocking_failures,
            "summary": {
                "check_count": len(checks),
                "passed_checks": sum(1 for check in checks if check["passed"]),
                "blocking_failures": len(blocking_failures),
                "reference_assets": len(reference_assets),
                "audio_track_configured": project.audio_track is not None,
                "audio_track_license": audio_license or None,
                "generated_assets": len(generated_assets),
                "workflow_templates": workflow_templates,
                "lora_ids": lora_ids,
                "license_registry": {
                    "source": self.license_registry.source,
                    "checked_count": registry_assessment["checked_count"],
                    "registered_count": registry_assessment["registered_count"],
                    "unregistered_count": registry_assessment["unregistered_count"],
                },
                "asset_rights": {
                    "enforced": asset_rights_enforced,
                    "checked_count": rights_assessment["checked_count"],
                    "registered_count": rights_assessment["registered_count"],
                    "unregistered_count": rights_assessment["unregistered_count"],
                },
            },
            "checks": checks,
            "registry": {
                "source": self.license_registry.source,
                "schema_version": self.license_registry.schema_version,
                **registry_assessment,
                "asset_rights": rights_assessment,
            },
        }

    def export_project_compliance(
        self,
        project_id: str,
        *,
        actor: str = "compliance-officer",
    ) -> dict[str, Any]:
        project = self._project(project_id)
        self._ensure_active(project)
        output_dir = self.output_root / project_id
        output_dir.mkdir(parents=True, exist_ok=True)
        self._record_event(
            project,
            action="project.compliance_exported",
            actor=actor,
            message="Project compliance report exported.",
        )
        path = self._compliance_report(project_id)
        report = self.project_compliance(project_id)
        path.write_text(
            json.dumps(report, ensure_ascii=True, indent=2),
            encoding="utf-8",
        )
        self._persist()
        return {
            "project_id": project_id,
            "compliance_report": str(path),
            "report": report,
        }

    def distribution_report(self, project_id: str) -> dict[str, Any]:
        project = self._project(project_id)
        deliveries = copy.deepcopy(project.deliveries)
        feedback = copy.deepcopy(project.delivery_feedback)
        latest = deliveries[-1] if deliveries else None
        summary = self._delivery_summary(deliveries)
        return {
            "schema_version": "mediaforge-distribution-v1",
            "project_id": project_id,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "report_path": str(self._distribution_report(project_id)),
            "release": project.release,
            "count": len(deliveries),
            "summary": summary,
            "delivered_channels": sorted(
                {
                    delivery["channel"]
                    for delivery in deliveries
                    if delivery.get("channel")
                }
            ),
            "latest": latest,
            "deliveries": deliveries,
            "feedback": {
                "summary": self._delivery_feedback_summary(feedback),
                "items": feedback,
            },
        }

    def export_distribution_report(
        self,
        project_id: str,
        *,
        actor: str = "delivery-ops",
    ) -> dict[str, Any]:
        project = self._project(project_id)
        self._ensure_active(project)
        output_dir = self.output_root / project_id
        output_dir.mkdir(parents=True, exist_ok=True)
        self._record_event(
            project,
            action="project.distribution_exported",
            actor=actor,
            message="Project distribution report exported.",
        )
        path = self._distribution_report(project_id)
        report = self.distribution_report(project_id)
        path.write_text(
            json.dumps(report, ensure_ascii=True, indent=2),
            encoding="utf-8",
        )
        self._persist()
        return {
            "project_id": project_id,
            "distribution_report": str(path),
            "report": report,
        }

    def dispatch_delivery(
        self,
        project_id: str,
        *,
        channel: str = "internal-review",
        recipient: str = "studio-archive",
        destination_uri: str | None = None,
        note: str = "",
        actor: str = "delivery-ops",
    ) -> dict[str, Any]:
        project = self._project(project_id)
        self._ensure_active(project)
        if project.release is None:
            raise WorkflowError("release project before dispatching delivery")
        if not project.delivery_package:
            raise WorkflowError("package delivery before dispatching delivery")
        latest_status = self._delivery_status(project.deliveries[-1] if project.deliveries else None)
        if latest_status in {"DELIVERED", "ACCEPTED"}:
            raise WorkflowError("acknowledge or resolve the existing delivery before dispatching again")
        verification = self.verify_delivery_package(
            project_id,
            actor="delivery-dispatch",
        )
        if not verification["verification"]["passed"]:
            raise WorkflowError("delivery package verification failed")
        compliance = self.project_compliance(project_id)
        if not compliance["passed"]:
            raise WorkflowError("project compliance report is not deliverable")
        continuity = self.project_continuity(project_id)
        if not continuity["passed"]:
            raise WorkflowError("project continuity report is not deliverable")
        try:
            storage_object = None
            if self.enterprise.storage.mode != "local":
                package = Path(project.delivery_package)
                storage_object = self.enterprise.storage.put(
                    package,
                    f"{project.brief.tenant_id}/{project_id}/{project.release['release_id']}/{package.name}",
                )
            dispatch = self.delivery_dispatcher.dispatch(
                Path(project.delivery_package),
                project_id=project_id,
                release_id=project.release["release_id"],
                channel=channel,
                recipient=recipient,
                destination_uri=destination_uri,
                note=note,
            )
        except (DeliveryDispatchError, EnterpriseConfigurationError) as exc:
            self._record_event(
                project,
                action="project.delivery_failed",
                actor=actor,
                message="Project delivery dispatch failed.",
                details={
                    "channel": channel,
                    "recipient": recipient,
                    "destination_uri": destination_uri,
                    "error": str(exc),
                },
            )
            self._persist()
            raise WorkflowError(str(exc)) from exc
        result = self.record_delivery(
            project_id,
            channel=channel,
            recipient=recipient,
            destination_uri=dispatch.destination_uri,
            note=note,
            actor=actor,
        )
        dispatch_data = dispatch.as_dict()
        if storage_object is not None:
            dispatch_data["storage_object"] = storage_object
        result["dispatch"] = dispatch_data
        delivery = result.get("delivery")
        if isinstance(delivery, dict):
            delivery["dispatch"] = dispatch_data
            receipt_path = Path(delivery["receipt_path"])
            receipt_path.write_text(
                json.dumps(delivery, ensure_ascii=True, indent=2),
                encoding="utf-8",
            )
        self._persist()
        return result

    def record_delivery(
        self,
        project_id: str,
        *,
        channel: str = "internal-review",
        recipient: str = "studio-archive",
        destination_uri: str | None = None,
        note: str = "",
        actor: str = "delivery-ops",
    ) -> dict[str, Any]:
        project = self._project(project_id)
        self._ensure_active(project)
        if project.release is None:
            raise WorkflowError("release project before recording delivery")
        if not project.delivery_package:
            raise WorkflowError("package delivery before recording delivery")
        latest_delivery = project.deliveries[-1] if project.deliveries else None
        latest_status = self._delivery_status(latest_delivery)
        if latest_status == "DELIVERED":
            raise WorkflowError(
                "acknowledge the current delivery before recording another"
            )
        if latest_status == "ACCEPTED":
            raise WorkflowError("delivery already accepted")
        verification = self.verify_delivery_package(
            project_id,
            actor="delivery-dispatch",
        )
        if not verification["verification"]["passed"]:
            raise WorkflowError("delivery package verification failed")
        compliance = self.project_compliance(project_id)
        if not compliance["passed"]:
            raise WorkflowError("project compliance report is not deliverable")
        continuity = self.project_continuity(project_id)
        if not continuity["passed"]:
            failed_checks = ", ".join(
                check["name"]
                for check in continuity["checks"]
                if not check["passed"]
            )
            raise WorkflowError(
                "project continuity report is not deliverable"
                f": {failed_checks}"
            )
        compliance_path = self._compliance_report(project_id)
        compliance_path.write_text(
            json.dumps(compliance, ensure_ascii=True, indent=2),
            encoding="utf-8",
        )
        continuity_path = self._continuity_report(project_id)
        continuity_path.write_text(
            json.dumps(continuity, ensure_ascii=True, indent=2),
            encoding="utf-8",
        )

        package_path = Path(project.delivery_package)
        timestamp = datetime.now(timezone.utc)
        delivery_id = f"delivery_{timestamp.strftime('%Y%m%d%H%M%S')}_{uuid4().hex[:8]}"
        output_dir = self.output_root / project_id / "deliveries"
        output_dir.mkdir(parents=True, exist_ok=True)
        receipt_path = output_dir / f"{delivery_id}.json"
        destination = (
            destination_uri
            or f"mediaforge://{channel.strip() or 'internal-review'}/{project_id}"
        )
        receipt = {
            "schema_version": "mediaforge-delivery-receipt-v1",
            "delivery_id": delivery_id,
            "project_id": project_id,
            "release_id": project.release["release_id"],
            "status": "DELIVERED",
            "channel": channel,
            "recipient": recipient,
            "destination_uri": destination,
            "note": note,
            "actor": actor,
            "delivered_at": timestamp.isoformat(),
            "acknowledged_at": None,
            "acknowledged_by": None,
            "acknowledgement_note": None,
            "package_zip": project.delivery_package,
            "package_sha256": sha256_file(package_path),
            "package_size_bytes": package_path.stat().st_size,
            "package_verification_report": verification["verification_report"],
            "compliance_report": str(compliance_path),
            "continuity_report": str(continuity_path),
            "final_mp4": project.final_mp4,
        }
        receipt_path.write_text(
            json.dumps(receipt, ensure_ascii=True, indent=2),
            encoding="utf-8",
        )
        delivery_record = {
            **receipt,
            "receipt_path": str(receipt_path),
        }
        project.deliveries.append(delivery_record)
        self._record_event(
            project,
            action="project.delivered",
            actor=actor,
            message=f"Release delivered to {channel}.",
            details={
                "delivery_id": delivery_id,
                "channel": channel,
                "recipient": recipient,
                "receipt_path": str(receipt_path),
            },
        )
        distribution = self.distribution_report(project_id)
        distribution_path = self._distribution_report(project_id)
        distribution_path.write_text(
            json.dumps(distribution, ensure_ascii=True, indent=2),
            encoding="utf-8",
        )
        self._persist()
        return {
            "project_id": project_id,
            "delivery": delivery_record,
            "receipt_path": str(receipt_path),
            "distribution_report": str(distribution_path),
            "distribution": distribution,
        }

    def acknowledge_delivery(
        self,
        project_id: str,
        delivery_id: str,
        *,
        accepted: bool = True,
        note: str = "",
        actor: str = "delivery-recipient",
    ) -> dict[str, Any]:
        project = self._project(project_id)
        self._ensure_active(project)
        if project.release is None:
            raise WorkflowError("release project before acknowledging delivery")
        delivery = self._delivery_record(project, delivery_id)
        if self._delivery_status(delivery) != "DELIVERED":
            raise WorkflowError("delivery is already finalized")
        receipt_path_value = delivery.get("receipt_path")
        if not receipt_path_value:
            raise WorkflowError("delivery receipt path is unavailable")

        timestamp = datetime.now(timezone.utc)
        outcome = "accepted" if accepted else "rejected"
        delivery["status"] = "ACCEPTED" if accepted else "REJECTED"
        delivery["acknowledged_at"] = timestamp.isoformat()
        delivery["acknowledged_by"] = actor
        delivery["acknowledgement_note"] = note

        receipt_path = Path(receipt_path_value)
        receipt_path.parent.mkdir(parents=True, exist_ok=True)
        receipt_path.write_text(
            json.dumps(
                self._delivery_receipt_payload(delivery),
                ensure_ascii=True,
                indent=2,
            ),
            encoding="utf-8",
        )
        self._record_event(
            project,
            action=(
                "project.delivery_accepted"
                if accepted
                else "project.delivery_rejected"
            ),
            actor=actor,
            message=f"Delivery {delivery_id} {outcome}.",
            details={
                "delivery_id": delivery_id,
                "status": delivery["status"],
                "receipt_path": str(receipt_path),
            },
        )
        distribution = self.distribution_report(project_id)
        distribution_path = self._distribution_report(project_id)
        distribution_path.write_text(
            json.dumps(distribution, ensure_ascii=True, indent=2),
            encoding="utf-8",
        )
        self._persist()
        return {
            "project_id": project_id,
            "delivery": copy.deepcopy(delivery),
            "receipt_path": str(receipt_path),
            "distribution_report": str(distribution_path),
            "distribution": distribution,
        }

    def delivery_feedback_report(
        self,
        project_id: str,
        *,
        delivery_id: str | None = None,
    ) -> dict[str, Any]:
        project = self._project(project_id)
        clean_delivery_id = str(delivery_id or "").strip()
        if clean_delivery_id:
            self._delivery_record(project, clean_delivery_id)
        items = [
            copy.deepcopy(item)
            for item in project.delivery_feedback
            if not clean_delivery_id or item.get("delivery_id") == clean_delivery_id
        ]
        items.sort(key=lambda item: str(item.get("submitted_at") or ""), reverse=True)
        return {
            "schema_version": "mediaforge-delivery-feedback-v1",
            "project_id": project_id,
            "delivery_id": clean_delivery_id or None,
            "summary": self._delivery_feedback_summary(items),
            "items": items,
        }

    def submit_delivery_feedback(
        self,
        project_id: str,
        *,
        delivery_id: str,
        target_type: str = "project",
        target_id: str | None = None,
        category: str = "other",
        severity: str = "NORMAL",
        verdict: str = "REQUEST_CHANGES",
        comment: str,
        rating: float | None = None,
        assignee: str | None = None,
        actor: str = "delivery-recipient",
    ) -> dict[str, Any]:
        project = self._project(project_id)
        self._ensure_active(project)
        delivery = self._delivery_record(project, str(delivery_id or "").strip())
        clean_type = str(target_type or "").strip().lower()
        clean_target = str(target_id or "").strip()
        if clean_type not in {"project", "shot"}:
            raise WorkflowError("delivery feedback target_type must be project or shot")
        if clean_type == "project":
            if clean_target and clean_target != project_id:
                raise WorkflowError("project delivery feedback target_id must match project_id")
            clean_target = project_id
        else:
            if not clean_target:
                raise WorkflowError("shot delivery feedback requires target_id")
            self._shot(project, clean_target)
        clean_category = str(category or "").strip().lower()
        if clean_category not in {
            "story", "visual", "audio", "continuity", "timing", "brand", "other"
        }:
            raise WorkflowError("unsupported delivery feedback category")
        clean_severity = str(severity or "").strip().upper()
        if clean_severity not in {"LOW", "NORMAL", "HIGH", "BLOCKER"}:
            raise WorkflowError("delivery feedback severity must be LOW, NORMAL, HIGH, or BLOCKER")
        clean_verdict = str(verdict or "").strip().upper()
        if clean_verdict not in {"APPROVE", "REQUEST_CHANGES", "QUESTION"}:
            raise WorkflowError("delivery feedback verdict must be APPROVE, REQUEST_CHANGES, or QUESTION")
        clean_comment = str(comment or "").strip()
        if not clean_comment or len(clean_comment) > 4_000:
            raise WorkflowError("delivery feedback comment must contain 1 to 4000 characters")
        if rating is not None and not 1 <= float(rating) <= 5:
            raise WorkflowError("delivery feedback rating must be between 1 and 5")
        clean_assignee = str(assignee or "").strip()
        if len(clean_assignee) > 160:
            raise WorkflowError("delivery feedback assignee must be <= 160 characters")
        timestamp = datetime.now(timezone.utc).isoformat()
        feedback = {
            "feedback_id": f"feedback_{uuid4().hex[:16]}",
            "delivery_id": delivery["delivery_id"],
            "target_type": clean_type,
            "target_id": clean_target,
            "category": clean_category,
            "severity": clean_severity,
            "verdict": clean_verdict,
            "rating": round(float(rating), 2) if rating is not None else None,
            "comment": clean_comment,
            "assignee": clean_assignee or None,
            "status": "OPEN",
            "submitted_by": str(actor or "delivery-recipient").strip() or "delivery-recipient",
            "submitted_at": timestamp,
            "updated_at": timestamp,
            "resolution": None,
            "resolved_by": None,
            "resolved_at": None,
        }
        project.delivery_feedback.append(feedback)
        self._record_event(
            project,
            action="delivery.feedback_recorded",
            actor=feedback["submitted_by"],
            message=f"Delivery feedback {feedback['feedback_id']} recorded.",
            shot_id=clean_target if clean_type == "shot" else None,
            details={
                key: feedback[key]
                for key in (
                    "feedback_id", "delivery_id", "target_type", "target_id",
                    "category", "severity", "verdict", "rating", "assignee",
                )
            },
        )
        self._persist()
        return {
            "project_id": project_id,
            "feedback": copy.deepcopy(feedback),
            "report": self.delivery_feedback_report(project_id),
        }

    def triage_delivery_feedback(
        self,
        project_id: str,
        feedback_id: str,
        *,
        status: str,
        resolution: str = "",
        assignee: str | None = None,
        actor: str = "delivery-owner",
    ) -> dict[str, Any]:
        project = self._project(project_id)
        self._ensure_active(project)
        feedback = next(
            (item for item in project.delivery_feedback if item.get("feedback_id") == feedback_id),
            None,
        )
        if feedback is None:
            raise WorkflowError("delivery feedback was not found")
        clean_status = str(status or "").strip().upper()
        if clean_status not in {"ACKNOWLEDGED", "RESOLVED", "DISMISSED"}:
            raise WorkflowError("delivery feedback status must be ACKNOWLEDGED, RESOLVED, or DISMISSED")
        previous_status = str(feedback.get("status") or "OPEN").upper()
        if previous_status in {"RESOLVED", "DISMISSED"} and clean_status != previous_status:
            raise WorkflowError("closed delivery feedback cannot be reopened")
        clean_resolution = str(resolution or "").strip()
        if len(clean_resolution) > 4_000:
            raise WorkflowError("delivery feedback resolution must be <= 4000 characters")
        if clean_status in {"RESOLVED", "DISMISSED"} and not clean_resolution:
            raise WorkflowError("closing delivery feedback requires a resolution")
        if assignee is not None:
            clean_assignee = str(assignee).strip()
            if len(clean_assignee) > 160:
                raise WorkflowError("delivery feedback assignee must be <= 160 characters")
            feedback["assignee"] = clean_assignee or None
        timestamp = datetime.now(timezone.utc).isoformat()
        feedback["status"] = clean_status
        feedback["updated_at"] = timestamp
        if clean_resolution:
            feedback["resolution"] = clean_resolution
        if clean_status in {"RESOLVED", "DISMISSED"}:
            feedback["resolved_by"] = str(actor or "delivery-owner").strip() or "delivery-owner"
            feedback["resolved_at"] = timestamp
        self._record_event(
            project,
            action="delivery.feedback_triaged",
            actor=str(actor or "delivery-owner").strip() or "delivery-owner",
            message=f"Delivery feedback {feedback_id} marked {clean_status}.",
            shot_id=feedback["target_id"] if feedback.get("target_type") == "shot" else None,
            details={
                "feedback_id": feedback_id,
                "previous_status": previous_status,
                "status": clean_status,
                "assignee": feedback.get("assignee"),
            },
        )
        self._persist()
        return {
            "project_id": project_id,
            "feedback": copy.deepcopy(feedback),
            "report": self.delivery_feedback_report(project_id),
        }

    def delivery_feedback_gate(self, project_id: str) -> dict[str, Any]:
        """Return the closeout gate for unresolved stakeholder feedback.

        Questions can remain open for a later clarification, but an unresolved
        change request or blocker must be explicitly resolved or dismissed
        before the project is archived.
        """
        project = self._project(project_id)
        unresolved = [
            copy.deepcopy(item)
            for item in project.delivery_feedback
            if str(item.get("status") or "OPEN").upper()
            in {"OPEN", "ACKNOWLEDGED"}
        ]
        blocking = [
            item
            for item in unresolved
            if str(item.get("severity") or "").upper() == "BLOCKER"
            or str(item.get("verdict") or "").upper() == "REQUEST_CHANGES"
        ]
        return {
            "passed": not blocking,
            "unresolved_count": len(unresolved),
            "blocking_count": len(blocking),
            "blocking_feedback_ids": [
                str(item.get("feedback_id"))
                for item in blocking
                if item.get("feedback_id")
            ],
            "blocking_reasons": sorted(
                {
                    "阻断" if str(item.get("severity") or "").upper() == "BLOCKER" else "需要修改"
                    for item in blocking
                }
            ),
        }

    def project_closeout(self, project_id: str) -> dict[str, Any]:
        project = self._project(project_id)
        deliveries = copy.deepcopy(project.deliveries)
        latest_delivery = deliveries[-1] if deliveries else None
        delivery_summary = self._delivery_summary(deliveries)
        delivery_verified, verification_report = self._delivery_verification_state(
            project
        )
        compliance = self.project_compliance(project_id)
        closeout_report = self._closeout_report(project_id)
        acceptance_report = self._acceptance_report(project_id)
        continuity = self.project_continuity(project_id)
        feedback_gate = self.delivery_feedback_gate(project_id)
        production_report = self.output_root / project_id / "production-report.json"
        provenance_report = self._provenance_report(project_id)
        distribution_report = self._distribution_report(project_id)
        audit_events = [
            self._audit_event_view(event)
            for event in project.audit_events
        ]
        return {
            "schema_version": "mediaforge-closeout-v1",
            "project_id": project_id,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "report_path": str(closeout_report),
            "project": {
                "project_id": project.brief.project_id,
                "title": project.brief.title,
                "status": project.status,
                "archived": project.archived_at is not None,
                "archived_at": (
                    project.archived_at.isoformat()
                    if project.archived_at
                    else None
                ),
                "created_at": project.created_at.isoformat(),
                "release": project.release,
                "final_mp4": project.final_mp4,
                "delivery_package": project.delivery_package,
                "acceptance_report": str(acceptance_report),
            },
            "summary": {
                "shot_count": len(project.shots),
                "approved_shots": sum(
                    1
                    for runtime in project.shots.values()
                    if runtime.review_status == ReviewStatus.APPROVED
                ),
                "generated_shots": sum(
                    1
                    for runtime in project.shots.values()
                    if runtime.current_artifact
                ),
                "delivery_count": len(deliveries),
                "delivery_pending_count": delivery_summary["pending_count"],
                "delivery_accepted_count": delivery_summary["accepted_count"],
                "delivery_rejected_count": delivery_summary["rejected_count"],
                "delivery_verified": delivery_verified,
                "compliance_passed": compliance["passed"],
                "continuity_passed": continuity["passed"],
                "delivery_feedback_gate_passed": feedback_gate["passed"],
                "delivery_feedback_blocking_count": feedback_gate["blocking_count"],
                "audit_event_count": len(project.audit_events),
                "archived": project.archived_at is not None,
            },
            "delivery": {
                "latest": latest_delivery,
                "summary": delivery_summary,
                "delivery_verified": delivery_verified,
                "delivery_verification_report": (
                    str(verification_report)
                    if verification_report.is_file()
                    else None
                ),
            },
            "references": {
                "production_report": (
                    str(production_report) if production_report.is_file() else None
                ),
                "provenance_report": (
                    str(provenance_report) if provenance_report.is_file() else None
                ),
                "distribution_report": (
                    str(distribution_report) if distribution_report.is_file() else None
                ),
                "acceptance_report": str(acceptance_report),
                "compliance_report": self._compliance_report(project_id).as_posix()
                if self._compliance_report(project_id).is_file()
                else None,
                "continuity_report": self._continuity_report(project_id).as_posix()
                if self._continuity_report(project_id).is_file()
                else None,
            },
            "governance": {
                "compliance": compliance,
                "continuity": continuity,
                "delivery_feedback_gate": feedback_gate,
                "distribution": self.distribution_report(project_id),
                "provenance": self.project_provenance(project_id),
            },
            "cost": self._cost_report(project),
            "audit": {
                "count": len(project.audit_events),
                "latest_events": audit_events[-20:],
            },
            "certificate": {
                "report_path": str(acceptance_report),
                "latest_delivery_id": (
                    latest_delivery["delivery_id"] if latest_delivery else None
                ),
                "latest_delivery_status": self._delivery_status(latest_delivery),
            },
        }

    def project_acceptance(self, project_id: str) -> dict[str, Any]:
        project = self._project(project_id)
        deliveries = copy.deepcopy(project.deliveries)
        latest_delivery = deliveries[-1] if deliveries else None
        if project.release is None:
            raise WorkflowError("release project before generating acceptance")
        if project.archived_at is None:
            raise WorkflowError("close the project before generating acceptance")
        if self._delivery_status(latest_delivery) != "ACCEPTED":
            raise WorkflowError("accept the delivery before generating acceptance")

        delivery_summary = self._delivery_summary(deliveries)
        delivery_verified, verification_report = self._delivery_verification_state(
            project
        )
        if not delivery_verified:
            raise WorkflowError("verify the delivery package before generating acceptance")
        compliance = self.project_compliance(project_id)
        if not compliance["passed"]:
            raise WorkflowError("resolve compliance before generating acceptance")
        continuity = self.project_continuity(project_id)
        if not continuity["passed"]:
            raise WorkflowError("resolve continuity before generating acceptance")

        closeout_report = self._closeout_report(project_id)
        if not closeout_report.is_file():
            raise WorkflowError("close the project before generating acceptance")
        acceptance_report = self._acceptance_report(project_id)
        production_report = self.output_root / project_id / "production-report.json"
        provenance_report = self._provenance_report(project_id)
        distribution_report = self._distribution_report(project_id)
        close_event = next(
            (
                event
                for event in reversed(project.audit_events)
                if event.action == "project.closed"
            ),
            None,
        )
        audit_events = [
            self._audit_event_view(event)
            for event in project.audit_events
        ]
        return {
            "schema_version": "mediaforge-acceptance-certificate-v1",
            "certificate_id": f"acceptance_{project_id}",
            "project_id": project_id,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "report_path": str(acceptance_report),
            "project": {
                "project_id": project.brief.project_id,
                "title": project.brief.title,
                "status": project.status,
                "archived": project.archived_at is not None,
                "archived_at": (
                    project.archived_at.isoformat()
                    if project.archived_at
                    else None
                ),
                "created_at": project.created_at.isoformat(),
                "release": project.release,
                "final_mp4": project.final_mp4,
                "delivery_package": project.delivery_package,
                "closeout_report": str(closeout_report),
            },
            "signoff": {
                "status": "ACCEPTED",
                "accepted_by": latest_delivery.get("acknowledged_by")
                if latest_delivery
                else None,
                "accepted_at": latest_delivery.get("acknowledged_at")
                if latest_delivery
                else None,
                "closed_by": close_event.actor if close_event else None,
                "closed_at": (
                    close_event.occurred_at.isoformat()
                    if close_event
                    else project.archived_at.isoformat()
                    if project.archived_at
                    else None
                ),
                "delivery_id": latest_delivery["delivery_id"]
                if latest_delivery
                else None,
                "release_id": project.release["release_id"]
                if project.release
                else None,
            },
            "summary": {
                "shot_count": len(project.shots),
                "approved_shots": sum(
                    1
                    for runtime in project.shots.values()
                    if runtime.review_status == ReviewStatus.APPROVED
                ),
                "generated_shots": sum(
                    1
                    for runtime in project.shots.values()
                    if runtime.current_artifact
                ),
                "delivery_count": len(deliveries),
                "delivery_pending_count": delivery_summary["pending_count"],
                "delivery_accepted_count": delivery_summary["accepted_count"],
                "delivery_rejected_count": delivery_summary["rejected_count"],
                "delivery_verified": delivery_verified,
                "compliance_passed": compliance["passed"],
                "continuity_passed": continuity["passed"],
                "archived": project.archived_at is not None,
                "audit_event_count": len(project.audit_events),
            },
            "evidence": {
                "production_report": (
                    str(production_report) if production_report.is_file() else None
                ),
                "provenance_report": (
                    str(provenance_report) if provenance_report.is_file() else None
                ),
                "distribution_report": (
                    str(distribution_report) if distribution_report.is_file() else None
                ),
                "delivery_verification_report": (
                    str(verification_report)
                    if verification_report.is_file()
                    else None
                ),
                "closeout_report": str(closeout_report),
                "compliance_report": self._compliance_report(project_id).as_posix()
                if self._compliance_report(project_id).is_file()
                else None,
                "continuity_report": self._continuity_report(project_id).as_posix()
                if self._continuity_report(project_id).is_file()
                else None,
            },
            "governance": {
                "compliance": compliance,
                "continuity": continuity,
                "distribution": self.distribution_report(project_id),
                "provenance": self.project_provenance(project_id),
                "production": self.production_report(project_id),
            },
            "cost": self._cost_report(project),
            "audit": {
                "count": len(project.audit_events),
                "latest_events": audit_events[-20:],
            },
        }

    def export_project_acceptance(
        self,
        project_id: str,
        *,
        actor: str = "studio-governance",
    ) -> dict[str, Any]:
        project = self._project(project_id)
        report = self.project_acceptance(project_id)
        path = self._acceptance_report(project_id)
        path.write_text(
            json.dumps(report, ensure_ascii=True, indent=2),
            encoding="utf-8",
        )
        self._record_event(
            project,
            action="project.acceptance_exported",
            actor=actor,
            message="Acceptance certificate exported.",
            details={"acceptance_report": str(path)},
        )
        self._persist()
        return {
            "project_id": project_id,
            "acceptance_report": str(path),
            "report": report,
        }

    def build_archive_package(
        self,
        project_id: str,
        *,
        actor: str = "archive-service",
    ) -> dict[str, Any]:
        project = self._project(project_id)
        if project.archived_at is None:
            raise WorkflowError("close the project before building archive package")
        if not project.final_mp4 or not Path(project.final_mp4).is_file():
            raise WorkflowError("final MP4 is missing")
        if not project.delivery_package or not Path(project.delivery_package).is_file():
            raise WorkflowError("delivery package is missing")
        delivery_verified, delivery_verification_report = (
            self._delivery_verification_state(project)
        )
        if not delivery_verified:
            raise WorkflowError("verify the delivery package before archiving")

        output_dir = self.output_root / project_id
        output_dir.mkdir(parents=True, exist_ok=True)
        archive_path = self._archive_package(project_id)
        closeout_report = self._closeout_report(project_id)
        acceptance_report = self._acceptance_report(project_id)
        if not closeout_report.is_file() or not acceptance_report.is_file():
            raise WorkflowError("closeout and acceptance reports are required")
        subtitle_path, _, _ = self._ensure_project_subtitles(project)
        self.export_project_snapshot(project_id, actor=actor)
        self._write_governance_files(project_id)
        self._write_audit_csv_file(project_id)
        required_reports = {
            "governance/closeout-report.json": closeout_report,
            "governance/acceptance-report.json": acceptance_report,
            "governance/delivery-verification.json": delivery_verification_report,
            "governance/production-report.json": self.output_root / project_id / "production-report.json",
            "governance/provenance-report.json": self._provenance_report(project_id),
            "governance/compliance-report.json": self._compliance_report(project_id),
            "governance/continuity-report.json": self._continuity_report(project_id),
            "governance/distribution-report.json": self._distribution_report(project_id),
            "governance/retrospective-report.json": self._retrospective_report(project_id),
            "governance/audit-log.json": self.output_root / project_id / "audit-log.json",
            "governance/audit-integrity.json": self.output_root / project_id / "audit-integrity.json",
            "governance/audit-anchors.json": self.output_root / project_id / "audit-anchors.json",
            "governance/content-credentials.json": self.output_root / project_id / "content-credentials.json",
            "governance/provider-contract.json": self.output_root / project_id / "provider-contract.json",
            "governance/audit-log.csv": self.output_root / project_id / "audit-log.csv",
            "governance/project-snapshot.json": self.output_root / project_id / "project-snapshot.json",
            "governance/trace-report.json": self.output_root / project_id / "trace-report.json",
        }
        missing = [
            arcname
            for arcname, path in required_reports.items()
            if not path.is_file()
        ]
        if missing:
            raise WorkflowError(
                "archive evidence is missing: " + ", ".join(sorted(missing))
            )

        sources: dict[Path, str] = {}

        def add_source(path: Path, arcname: str) -> None:
            resolved = path.resolve()
            if not resolved.is_file():
                raise WorkflowError(f"archive asset is missing: {path}")
            if not self._safe_archive_name(arcname):
                raise WorkflowError(f"unsafe archive path: {arcname}")
            sources[resolved] = arcname

        add_source(Path(project.final_mp4), "deliverables/final_sample.mp4")
        add_source(subtitle_path, "deliverables/final_subtitles.srt")
        if project.audio_track:
            audio_path = Path(project.audio_track)
            add_source(
                audio_path,
                f"deliverables/final_audio{audio_path.suffix.lower()}",
            )
        add_source(Path(project.delivery_package), "deliverables/delivery-package.zip")
        for arcname, path in required_reports.items():
            add_source(path, arcname)
        for credential in project.content_credentials:
            for path_key in ("manifest_path", "c2pa_manifest_path", "signed_output"):
                path_value = credential.get(path_key)
                if path_value and Path(str(path_value)).is_file():
                    add_source(
                        Path(str(path_value)),
                        f"content-credentials/{Path(str(path_value)).name}",
                    )

        files = [
            {
                "path": arcname,
                "size_bytes": path.stat().st_size,
                "sha256": sha256_file(path),
            }
            for path, arcname in sorted(
                sources.items(),
                key=lambda item: item[1],
            )
        ]
        summary = {
            "schema_version": "mediaforge-archive-package-v1",
            "project_id": project_id,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "archived_at": (
                project.archived_at.isoformat()
                if project.archived_at
                else None
            ),
            "release": project.release,
            "latest_delivery": (
                project.deliveries[-1] if project.deliveries else None
            ),
            "final_mp4": project.final_mp4,
            "subtitle_srt": str(subtitle_path),
            "audio_track": project.audio_track,
            "audio_track_metadata": copy.deepcopy(project.audio_track_metadata),
            "delivery_package": project.delivery_package,
            "closeout_report": str(closeout_report),
            "acceptance_report": str(acceptance_report),
            "continuity": self.project_continuity(project_id),
            "audit_event_count": len(project.audit_events),
            "files": files,
        }

        with zipfile.ZipFile(
            archive_path,
            mode="w",
            compression=zipfile.ZIP_DEFLATED,
        ) as archive:
            for path, arcname in sorted(sources.items(), key=lambda item: item[1]):
                archive.write(path, arcname)
            archive.writestr(
                "archive-summary.json",
                json.dumps(summary, ensure_ascii=True, indent=2),
            )

        with zipfile.ZipFile(archive_path) as archive:
            verification = self._verify_manifest_archive(
                archive,
                summary_name="archive-summary.json",
                schema_version="mediaforge-archive-verification-v1",
                expected_project_id=project_id,
            )

        verification_report = self._archive_verification_report(project_id)
        verification["report_path"] = str(verification_report)
        verification_report.write_text(
            json.dumps(verification, ensure_ascii=True, indent=2),
            encoding="utf-8",
        )
        if not verification["passed"]:
            raise WorkflowError(
                "archive package verification failed: "
                f"{self._verification_error_summary(verification)}"
            )

        project.archive_package = str(archive_path)
        self._record_event(
            project,
            action="project.archive_packaged",
            actor=actor,
            message="Final archive package prepared.",
            details={
                "archive_package": project.archive_package,
                "verification_report": str(verification_report),
                "verified_files": verification["manifest_file_count"],
            },
        )
        self._persist()
        return {
            "project_id": project_id,
            "archive_package": project.archive_package,
            "archive_verification_report": str(verification_report),
            "size_bytes": archive_path.stat().st_size,
            "file_count": len(files) + 1,
            "summary": summary,
            "verification": verification,
        }

    def verify_archive_package(
        self,
        project_id: str,
        *,
        actor: str = "archive-qa",
    ) -> dict[str, Any]:
        project = self._project(project_id)
        if not project.archive_package:
            raise WorkflowError("build archive package before verification")
        archive_path = Path(project.archive_package)
        if not archive_path.is_file():
            raise WorkflowError("archive package is missing")
        with zipfile.ZipFile(archive_path) as archive:
            verification = self._verify_manifest_archive(
                archive,
                summary_name="archive-summary.json",
                schema_version="mediaforge-archive-verification-v1",
                expected_project_id=project_id,
            )
        verification_report = self._archive_verification_report(project_id)
        verification["report_path"] = str(verification_report)
        verification_report.write_text(
            json.dumps(verification, ensure_ascii=True, indent=2),
            encoding="utf-8",
        )
        self._record_event(
            project,
            action="project.archive_verified",
            actor=actor,
            message="Final archive package verification completed.",
            details={
                "archive_package": str(archive_path),
                "verification_report": str(verification_report),
                "passed": verification["passed"],
                "failed_checks": [
                    check["name"]
                    for check in verification["checks"]
                    if not check["passed"]
                ],
            },
        )
        self._persist()
        return {
            "project_id": project_id,
            "archive_package": str(archive_path),
            "archive_verification_report": str(verification_report),
            "verification": verification,
        }

    def closeout_project(
        self,
        project_id: str,
        *,
        actor: str = "studio-publisher",
    ) -> dict[str, Any]:
        project = self._project(project_id)
        latest_delivery = project.deliveries[-1] if project.deliveries else None
        if project.status != ProjectStatus.EXPORTED or not project.final_mp4:
            raise WorkflowError("export project before closing it out")
        if not project.delivery_package:
            raise WorkflowError("package delivery before closing it out")
        delivery_verified, _ = self._delivery_verification_state(project)
        if not delivery_verified:
            raise WorkflowError("verify the package before closing the project")
        compliance = self.project_compliance(project_id)
        if not compliance["passed"]:
            raise WorkflowError("resolve compliance before closing the project")
        if project.release is None:
            raise WorkflowError("release project before closing it out")
        if self._delivery_status(latest_delivery) != "ACCEPTED":
            raise WorkflowError("accept the delivery before closing the project")
        feedback_gate = self.delivery_feedback_gate(project_id)
        if not feedback_gate["passed"]:
            ids = ", ".join(feedback_gate["blocking_feedback_ids"])
            raise WorkflowError(
                "resolve or dismiss blocking delivery feedback before closing the project"
                + (f": {ids}" if ids else "")
            )

        closeout_report = self._closeout_report(project_id)
        acceptance_report = self._acceptance_report(project_id)
        if (
            closeout_report.is_file()
            and acceptance_report.is_file()
            and project.archived_at is not None
        ):
            return {
                "project_id": project_id,
                "closeout_report": str(closeout_report),
                "acceptance_report": str(acceptance_report),
                "report": json.loads(closeout_report.read_text(encoding="utf-8")),
                "acceptance": json.loads(
                    acceptance_report.read_text(encoding="utf-8")
                ),
                "archived": True,
            }

        if project.archived_at is None:
            self.archive_project(project_id, actor=actor)
            project = self._project(project_id)

        self._record_event(
            project,
            action="project.closed",
            actor=actor,
            message="Project closed out and archived.",
            details={
                "closeout_report": str(closeout_report),
                "acceptance_report": str(acceptance_report),
            },
        )
        report = self.project_closeout(project_id)
        closeout_report.write_text(
            json.dumps(report, ensure_ascii=True, indent=2),
            encoding="utf-8",
        )
        acceptance = self.project_acceptance(project_id)
        acceptance_report.write_text(
            json.dumps(acceptance, ensure_ascii=True, indent=2),
            encoding="utf-8",
        )
        self._persist()
        return {
            "project_id": project_id,
            "closeout_report": str(closeout_report),
            "acceptance_report": str(acceptance_report),
            "report": report,
            "acceptance": acceptance,
            "archived": project.archived_at is not None,
        }

    def shot_route_report(
        self,
        project_id: str,
        shot_id: str,
    ) -> dict[str, Any]:
        project = self._project(project_id)
        runtime = self._shot(project, shot_id)
        decision: Any | None = None
        decision_error: str | None = None
        try:
            decision = self.router.select(runtime.spec)
        except ProviderRoutingError as exc:
            decision_error = str(exc)

        candidates: list[dict[str, Any]] = []
        for registration in self.router.registrations:
            supported = bool(
                registration.enabled
                and registration.provider.supports(
                    runtime.spec.provider_constraints.capability
                )
            )
            estimated_cost = None
            route_error = None
            within_budget = False
            if supported:
                try:
                    estimated_cost = registration.provider.estimate_cost(
                        runtime.spec
                    )
                    within_budget = (
                        estimated_cost
                        <= runtime.spec.provider_constraints.max_cost
                    )
                except Exception as exc:  # pragma: no cover - defensive
                    route_error = str(exc)
            candidate = {
                "provider": registration.provider.name,
                "enabled": registration.enabled,
                "priority": registration.priority,
                "supported": supported,
                "estimated_cost": (
                    round(float(estimated_cost), 4)
                    if estimated_cost is not None
                    else None
                ),
                "within_budget": within_budget,
                "selected": bool(
                    decision and decision.provider.name == registration.provider.name
                ),
                "reason": (
                    decision.reason
                    if decision and decision.provider.name == registration.provider.name
                    else route_error or (
                        "disabled" if not registration.enabled else "unsupported"
                    )
                ),
            }
            if route_error:
                candidate["error"] = route_error
            candidates.append(candidate)

        candidates.sort(
            key=lambda candidate: (
                not candidate["selected"],
                not candidate["supported"],
                float(candidate["estimated_cost"] or 0.0),
                -int(candidate["priority"]),
                candidate["provider"],
            )
        )

        return {
            "schema_version": "mediaforge-shot-route-v1",
            "project_id": project_id,
            "shot_id": shot_id,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "provider_status": self.provider_status,
            "budget": runtime.spec.provider_constraints.max_cost,
            "selected_provider": (
                decision.provider.name if decision else None
            ),
            "selected_estimated_cost": (
                round(decision.estimated_cost, 4) if decision else None
            ),
            "selected_reason": decision.reason if decision else None,
            "selection_error": decision_error,
            "candidates": candidates,
            "candidate_count": len(candidates),
            "supported_count": sum(1 for candidate in candidates if candidate["supported"]),
            "within_budget_count": sum(1 for candidate in candidates if candidate["within_budget"]),
        }

    def project_routes(self, project_id: str) -> dict[str, Any]:
        project = self._project(project_id)
        reports = [
            self.shot_route_report(project_id, runtime.shot.shot_id)
            for runtime in project.shots.values()
        ]
        return {
            "schema_version": "mediaforge-project-route-preview-v1",
            "project_id": project_id,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "count": len(reports),
            "selected_count": sum(1 for report in reports if report["selected_provider"]),
            "reports": reports,
        }

    def export_shot_route_report(
        self,
        project_id: str,
        shot_id: str,
        *,
        actor: str = "studio-user",
    ) -> dict[str, Any]:
        project = self._project(project_id)
        self._ensure_active(project)
        self._shot(project, shot_id)
        output_dir = self.output_root / project_id / "routes"
        output_dir.mkdir(parents=True, exist_ok=True)
        path = output_dir / f"{shot_id}-route.json"
        report = self.shot_route_report(project_id, shot_id)
        report["report_path"] = str(path)
        path.write_text(
            json.dumps(report, ensure_ascii=True, indent=2),
            encoding="utf-8",
        )
        self._record_event(
            project,
            action="shot.route_exported",
            actor=actor,
            message=f"{shot_id} route preview exported.",
            shot_id=shot_id,
            details={"route_report": str(path)},
        )
        self._persist()
        return {
            "project_id": project_id,
            "shot_id": shot_id,
            "route_report": str(path),
            "report": report,
        }

    def compare_shot_variants(
        self,
        project_id: str,
        shot_id: str,
        *,
        candidate_count: int = 2,
        actor: str = "studio-optimizer",
    ) -> dict[str, Any]:
        project = self._project(project_id)
        self._ensure_active(project)
        runtime = self._shot(project, shot_id)
        if project.status == ProjectStatus.DRAFT:
            raise WorkflowError("generate a plan before comparing shot variants")
        if not 1 <= candidate_count <= 4:
            raise WorkflowError("candidate_count must be between 1 and 4")
        if not self.provider_status.get("configured", False):
            raise WorkflowError(
                f"Provider is not available: {self.provider_status['message']}"
            )

        planned_variants: list[dict[str, Any]] = []
        starting_index = len(runtime.variants) + 1
        for offset in range(candidate_count):
            variant_index = starting_index + offset
            spec = self._variant_spec(runtime, variant_index)
            try:
                decision = self.router.select(spec)
            except ProviderRoutingError as exc:
                raise WorkflowError(
                    f"Provider is not available: {self.provider_status['message']}"
                ) from exc
            policy_report = self.policy.assess_shot(
                runtime.shot,
                spec,
                revision=runtime.revision,
            ).as_dict()
            project.policy_reports.append(policy_report)
            if not policy_report["passed"]:
                self._record_event(
                    project,
                    action="policy.variant_blocked",
                    actor="policy-engine",
                    message=f"{shot_id} A/B candidate failed policy checks.",
                    shot_id=shot_id,
                    details=policy_report,
                )
                self._persist()
                raise PolicyViolation(
                    f"content policy blocked {shot_id} A/B candidate",
                    policy_report,
                )
            planned_variants.append(
                {
                    "variant_index": variant_index,
                    "spec": spec,
                    "decision": decision,
                    "policy": policy_report,
                }
            )

        projected_spend = (
            self._cost_report(project)["spent"]
            + self._reserved_generation_cost(project)
            + sum(item["decision"].estimated_cost for item in planned_variants)
        )
        if projected_spend > project.brief.budget:
            raise WorkflowError(
                "project budget exceeded: "
                f"${projected_spend:.2f} > ${project.brief.budget:.2f}"
            )

        self._enforce_job_quota(
            project.brief.tenant_id,
            additional_jobs=len(planned_variants),
        )
        generated: list[dict[str, Any]] = []
        project.status = ProjectStatus.IN_PROGRESS
        for item in planned_variants:
            variant_index = item["variant_index"]
            spec = item["spec"]
            decision = item["decision"]
            variant_id = f"var_{uuid4().hex[:12]}"
            label = f"A/B {variant_index}"
            job = self.jobs.create(
                spec,
                (
                    f"{project_id}:{shot_id}:revision:{runtime.revision}:"
                    f"variant:{variant_index}"
                ),
                trace_id=project.trace_id,
            )
            if job.status == JobStatus.CREATED:
                self.jobs.transition(job.job_id, JobStatus.VALIDATED)
                self.jobs.transition(job.job_id, JobStatus.QUEUED)
                self.jobs.transition(job.job_id, JobStatus.ADMITTED)
                self.jobs.transition(job.job_id, JobStatus.RUNNING)
            elif job.status in {
                JobStatus.VALIDATED,
                JobStatus.QUEUED,
                JobStatus.ADMITTED,
            }:
                while job.status != JobStatus.RUNNING:
                    next_status = {
                        JobStatus.VALIDATED: JobStatus.QUEUED,
                        JobStatus.QUEUED: JobStatus.ADMITTED,
                        JobStatus.ADMITTED: JobStatus.RUNNING,
                    }[job.status]
                    self.jobs.transition(job.job_id, next_status)
            else:
                raise WorkflowError(f"variant job cannot run from {job.status}")

            route = {
                "provider": decision.provider.name,
                "estimated_cost": decision.estimated_cost,
                "reason": decision.reason,
            }
            self._record_event(
                project,
                action="shot.variant_submitted",
                actor=actor,
                message=f"{shot_id} {label} submitted to {decision.provider.name}.",
                shot_id=shot_id,
                details={
                    "variant_id": variant_id,
                    "job_id": job.job_id,
                    "estimated_cost": decision.estimated_cost,
                },
            )
            try:
                artifact = self._generate_artifact(
                    decision.provider,
                    spec,
                    job_id=job.job_id,
                    output_dir=self.output_root / project_id / "shots",
                    estimated_cost=decision.estimated_cost,
                    tenant_id=project.brief.tenant_id,
                    project_id=project_id,
                )
                quality_result = self._probe_artifact_media(artifact)
                quality = self._evaluate_artifact_quality(
                    artifact,
                    quality_result,
                    spec,
                )
                job.add_artifact(artifact)
                if quality["passed"]:
                    self.jobs.transition(job.job_id, JobStatus.SUCCEEDED)
                else:
                    self.jobs.transition(
                        job.job_id,
                        JobStatus.QUALITY_REJECTED,
                        reason=quality_result.error or "quality gate failed",
                    )
                variant = {
                    "variant_id": variant_id,
                    "label": label,
                    "shot_id": shot_id,
                    "revision": runtime.revision,
                    "job_id": job.job_id,
                    "status": job.status,
                    "artifact": artifact.model_dump(mode="json"),
                    "route": route,
                    "quality": quality,
                    "spec": spec.model_dump(mode="json"),
                    "score": self._score_variant(quality, route, spec),
                    "promoted": False,
                    "selected": False,
                    "created_at": datetime.now(timezone.utc).isoformat(),
                }
            except Exception as exc:
                if job.status == JobStatus.RUNNING:
                    self.jobs.transition(job.job_id, JobStatus.FAILED, reason=str(exc))
                variant = {
                    "variant_id": variant_id,
                    "label": label,
                    "shot_id": shot_id,
                    "revision": runtime.revision,
                    "job_id": job.job_id,
                    "status": job.status,
                    "artifact": None,
                    "route": route,
                    "quality": None,
                    "spec": spec.model_dump(mode="json"),
                    "score": {
                        "total": 0.0,
                        "breakdown": {"generation": 0.0},
                        "passed": False,
                    },
                    "error": str(exc),
                    "promoted": False,
                    "selected": False,
                    "created_at": datetime.now(timezone.utc).isoformat(),
                }
            runtime.variants.append(variant)
            generated.append(variant)

        comparison = self._build_shot_comparison(project, runtime)
        project.comparison_reports.append(comparison)
        self._record_event(
            project,
            action="shot.variants_compared",
            actor=actor,
            message=f"{shot_id} A/B comparison generated.",
            shot_id=shot_id,
            details={
                "comparison_id": comparison["comparison_id"],
                "candidate_count": len(generated),
                "recommended_variant_id": comparison["recommended_variant_id"],
            },
        )
        self._persist()
        return {
            "project_id": project_id,
            "shot_id": shot_id,
            "generated": generated,
            "comparison": comparison,
            "shot": self.shot_view(runtime),
        }

    def shot_comparison_report(self, project_id: str, shot_id: str) -> dict[str, Any]:
        project = self._project(project_id)
        runtime = self._shot(project, shot_id)
        latest = next(
            (
                report
                for report in reversed(project.comparison_reports)
                if report["shot_id"] == shot_id
            ),
            None,
        )
        return {
            "project_id": project_id,
            "shot_id": shot_id,
            "latest": latest,
            "current": self._build_shot_comparison(project, runtime),
            "history": [
                report
                for report in project.comparison_reports
                if report["shot_id"] == shot_id
            ],
        }

    def project_comparisons(self, project_id: str) -> dict[str, Any]:
        project = self._project(project_id)
        latest_by_shot: dict[str, dict[str, Any]] = {}
        for report in project.comparison_reports:
            latest_by_shot[report["shot_id"]] = report
        return {
            "project_id": project_id,
            "count": len(project.comparison_reports),
            "latest": (
                project.comparison_reports[-1]
                if project.comparison_reports
                else None
            ),
            "latest_by_shot": latest_by_shot,
            "reports": project.comparison_reports,
        }

    def export_shot_comparison(
        self,
        project_id: str,
        shot_id: str,
        *,
        actor: str = "studio-user",
    ) -> dict[str, Any]:
        project = self._project(project_id)
        self._ensure_active(project)
        self._shot(project, shot_id)
        output_dir = self.output_root / project_id / "comparisons"
        output_dir.mkdir(parents=True, exist_ok=True)
        path = output_dir / f"{shot_id}-comparison.json"
        report = self.shot_comparison_report(project_id, shot_id)
        report["report_path"] = str(path)
        path.write_text(
            json.dumps(report, ensure_ascii=True, indent=2),
            encoding="utf-8",
        )
        self._record_event(
            project,
            action="shot.comparison_exported",
            actor=actor,
            message=f"{shot_id} A/B comparison exported.",
            shot_id=shot_id,
            details={"comparison_report": str(path)},
        )
        self._persist()
        return {
            "project_id": project_id,
            "shot_id": shot_id,
            "comparison_report": str(path),
            "report": report,
        }

    def promote_shot_variant(
        self,
        project_id: str,
        shot_id: str,
        variant_id: str,
        *,
        actor: str = "studio-reviewer",
        comment: str = "",
    ) -> dict[str, Any]:
        project = self._project(project_id)
        self._ensure_active(project)
        runtime = self._shot(project, shot_id)
        variant = self._variant(runtime, variant_id)
        if not variant.get("artifact"):
            raise WorkflowError(f"variant has no artifact to promote: {variant_id}")
        if not variant.get("quality", {}).get("passed", False):
            raise WorkflowError(f"variant did not pass quality: {variant_id}")

        runtime.current_job_id = variant["job_id"]
        runtime.current_artifact = copy.deepcopy(variant["artifact"])
        runtime.route = copy.deepcopy(variant["route"])
        runtime.quality = copy.deepcopy(variant["quality"])
        runtime.review_status = ReviewStatus.PENDING
        for candidate in runtime.variants:
            candidate["selected"] = candidate["variant_id"] == variant_id
            if candidate["variant_id"] == variant_id:
                candidate["promoted"] = True
                candidate["promoted_at"] = datetime.now(timezone.utc).isoformat()

        if not any(
            entry.get("job_id") == variant["job_id"]
            for entry in runtime.artifact_history
        ):
            runtime.artifact_history.append(
                {
                    "revision": runtime.revision,
                    "job_id": variant["job_id"],
                    "variant_id": variant_id,
                    "artifact": runtime.current_artifact,
                    "route": runtime.route,
                    "quality": runtime.quality,
                    "promoted_from_variant": True,
                    "created_at": datetime.now(timezone.utc).isoformat(),
                }
            )
        runtime.reviews.append(
            ReviewRecord(
                status=ReviewStatus.PENDING,
                comment=comment,
                actor=actor,
                occurred_at=datetime.now(timezone.utc),
            )
        )
        project.status = ProjectStatus.IN_PROGRESS
        project.final_mp4 = None
        project.subtitle_srt = None
        project.delivery_package = None
        project.archive_package = None
        project.release = None
        comparison = self._build_shot_comparison(project, runtime)
        project.comparison_reports.append(comparison)
        self._record_event(
            project,
            action="shot.variant_promoted",
            actor=actor,
            message=f"{shot_id} promoted {variant_id} as the current version.",
            shot_id=shot_id,
            details={
                "variant_id": variant_id,
                "job_id": variant["job_id"],
                "comment": comment,
                "comparison_id": comparison["comparison_id"],
            },
        )
        self._persist()
        return {
            "project_id": project_id,
            "shot_id": shot_id,
            "variant_id": variant_id,
            "comparison": comparison,
            "shot": self.shot_view(runtime),
        }

    def generate_plan(
        self, project_id: str, *, memory_project_ids: list[str] | None = None,
        subject: str | None = None,
    ) -> dict[str, Any]:
        with self.planning.mutation_guard(project_id):
            return self._generate_plan(project_id, memory_project_ids=memory_project_ids, subject=subject)

    def _generate_plan(
        self, project_id: str, *, memory_project_ids: list[str] | None = None,
        subject: str | None = None,
    ) -> dict[str, Any]:
        if self.planning.has_active(project_id):
            raise PlanningError('approve or cancel the active planning draft first')
        project = self._project(project_id)
        self._ensure_active(project)
        brief = project.brief
        narrative_events = self.approved_narrative_events(project_id)
        adaptation_scenes = self.approved_adaptation_scenes(project_id)
        memory_context = []
        plan_with_context = getattr(self.story_planner, "plan_with_context", None)
        if self.memory_settings.enabled and callable(plan_with_context):
            memory_context = self.story_memory_search(
                project_id, f"{brief.premise} {brief.genre} {brief.style} {' '.join(brief.characters)}",
                source_project_ids=memory_project_ids, subject=subject,
            )["results"]
        if self.story_planner is None:
            story_bible = {
                "project_id": brief.project_id,
                "title": brief.title,
                "premise": brief.premise,
                "genre": brief.genre,
                "style": brief.style,
                "characters": [
                    {
                        "name": name,
                        "version": "v1",
                        "constraints": ["consistent appearance", "consistent wardrobe"],
                    }
                    for name in brief.characters
                ],
            }
            shots = self._build_shots(
                brief,
                narrative_events=narrative_events,
                adaptation_scenes=adaptation_scenes,
            )
        else:
            try:
                plan = plan_with_context(brief, memory_context) if callable(plan_with_context) else self.story_planner.plan(brief)
            except StoryPlannerError:
                raise
            except Exception as exc:
                raise WorkflowError(f"story planner failed: {exc}") from exc
            if not isinstance(plan, StoryPlan):
                raise WorkflowError("story planner returned an invalid plan")
            story_bible = copy.deepcopy(plan.story_bible)
            shots = list(plan.shots)
            if narrative_events or adaptation_scenes:
                shots = self._build_shots(
                    brief,
                    narrative_events=narrative_events,
                    adaptation_scenes=adaptation_scenes,
                )
            self._validate_story_plan(brief, shots)
        planned_runtimes, policy_reports = self._prepare_planned_shots(project, shots)
        blocked = [
            report
            for report in policy_reports
            if not report["passed"]
        ]
        project.policy_reports.extend(policy_reports)
        if blocked:
            self._record_event(
                project,
                action="policy.shots_blocked",
                actor="policy-engine",
                message=f"{len(blocked)} planned shot(s) failed policy checks.",
                details={"blocked": blocked},
            )
            self._persist()
            raise PolicyViolation(
                "content policy blocked planned shots",
                {"reports": policy_reports, "blocked": blocked},
            )
        story_bible["narrative_event_context"] = self._narrative_event_context(project)
        story_bible["adaptation_scene_context"] = self._adaptation_scene_context(project)
        story_bible["prompt_snapshots"] = self._active_prompt_snapshots(project)
        story_bible["memory_retrieval"] = {
            "enabled": self.memory_settings.enabled,
            "backend": self.story_memory.status_view()["backend"],
            "context_used": bool(memory_context),
            "sources": [{**{key: hit[key] for key in ("project_id", "kind", "sha256", "metadata")},
                         "truncated": hit.get("truncated", False),
                         "excerpt_sha256": hashlib.sha256(hit["content"].encode("utf-8")).hexdigest(),
                         "embedding_fingerprint": hit.get("embedding_fingerprint")}
                        for hit in memory_context],
        }
        self._invalidate_workflow_from(
            project,
            "script",
            reason="plan_regenerated",
            actor="story-agent",
        )
        project.story_bible = story_bible
        project.shots = planned_runtimes
        project.status = ProjectStatus.PLANNED
        self._record_event(
            project,
            action="project.planned",
            actor="story-agent",
            message=f"{len(project.shots)} shot cards planned.",
            details={
                "shot_count": len(project.shots),
                "prompt_snapshots": self._active_prompt_snapshots(project),
            },
        )
        self._record_event(
            project,
            action="policy.shots_checked",
            actor="policy-engine",
            message=f"{len(policy_reports)} shot card(s) passed policy checks.",
            details={"reports": policy_reports},
        )
        self._persist()
        return self.project_view(project_id)

    def _prepare_planned_shots(self, project, shots):
        runtimes, reports = {}, []
        for shot in shots:
            spec = self._compile_spec(shot, reference_assets=self._reference_assets_for_shot(project, shot))
            reports.append(self.policy.assess_shot(shot, spec).as_dict())
            runtimes[shot.shot_id] = ShotRuntime(shot=shot, spec=spec)
        return runtimes, reports

    def _adopt_planning_draft(self, project_id, state, actor):
        with self._state_lock:
            project = self._project(project_id)
            self._ensure_active(project)
            if self.planning._input_fingerprint(project_id) != state['input_fingerprint']:
                raise PlanningError('project changed after planning started; cancel this draft and create a new run')
            shots = [ShotCard.model_validate(shot) for shot in state['shots']]
            self._validate_story_plan(project.brief, shots)
            runtimes, reports = self._prepare_planned_shots(project, shots)
            specs = [runtime.spec.model_dump(mode='json') for runtime in runtimes.values()]
            if any(not report['passed'] for report in reports) or fingerprint(specs) != fingerprint(state['specs']):
                raise PlanningError('planning policy or compiled parameters changed; start a new run')
            if self.planning._input_fingerprint(project_id) != state['input_fingerprint']:
                raise PlanningError('project changed after planning started; cancel this draft and create a new run')
            candidate = copy.deepcopy(project)
            candidate.story_bible = copy.deepcopy(state['bible'])
            candidate.story_bible.update({
                'planning_run_id': state['run_id'], 'planning_graph_version': self.planning.version,
                'planning_trace': copy.deepcopy(state.get('trace', [])),
                'prompt_snapshots': self._active_prompt_snapshots(candidate),
                'memory_retrieval': {
                    'enabled': self.memory_settings.enabled, 'context_used': bool(state.get('memory')),
                    'backend': self.story_memory.status_view()['backend'],
                    'sources': [{**{key: hit.get(key) for key in ('project_id', 'kind', 'sha256', 'metadata', 'embedding_fingerprint')},
                                 'truncated': hit.get('truncated', False),
                                 'excerpt_sha256': hashlib.sha256(hit['content'].encode('utf-8')).hexdigest()}
                                for hit in state.get('memory', [])],
                },
            })
            candidate.shots = runtimes
            self._invalidate_workflow_from(
                candidate,
                "script",
                reason="planning_draft_adopted",
                actor=actor,
            )
            self._invalidate_postproduction(candidate)
            candidate.status = ProjectStatus.PLANNED
            candidate.policy_reports.extend(reports)
            self._record_event(
                candidate,
                action="planning.approved",
                actor=actor,
                message="Staged planning draft adopted after human review.",
                details={
                    "run_id": state["run_id"],
                    "graph_version": self.planning.version,
                    "revision": state.get("revision", 0),
                    "shot_count": len(shots),
                },
            )
            self.projects[project_id] = candidate
            try:
                self._persist()
            except Exception:
                self.projects[project_id] = project
                raise

    @staticmethod
    def _validate_story_plan(brief: CreativeBrief, shots: list[ShotCard]) -> None:
        if not shots or len(shots) > 12:
            raise WorkflowError("story planner must return between 1 and 12 shots")
        shot_ids = [shot.shot_id for shot in shots]
        if len(set(shot_ids)) != len(shot_ids):
            raise WorkflowError("story planner returned duplicate shot ids")
        allowed_characters = set(brief.characters)
        if any(
            shot.project_id != brief.project_id
            or any(character not in allowed_characters for character in shot.characters)
            for shot in shots
        ):
            raise WorkflowError("story planner returned an unknown project or character")
        if sum(shot.duration_seconds for shot in shots) != brief.duration_seconds:
            raise WorkflowError("story planner shot durations must equal the brief duration")

    def submit_shot(self, project_id: str, shot_id: str) -> dict[str, Any]:
        with self.planning.mutation_guard(project_id):
            return self._submit_shot(project_id, shot_id)

    def _submit_shot(self, project_id: str, shot_id: str) -> dict[str, Any]:
        if self.planning.has_active(project_id):
            raise PlanningError('approve or cancel the active planning draft first')
        project = self._project(project_id)
        self._ensure_active(project)
        runtime = self._shot(project, shot_id)
        if runtime.current_artifact and runtime.review_status != ReviewStatus.CHANGES_REQUESTED:
            return self.shot_view(runtime)
        if project.status == ProjectStatus.DRAFT:
            raise WorkflowError("generate a plan before submitting shots")
        self._require_production_stage_lock(project, "assets")
        if not self.provider_status.get("configured", False):
            raise WorkflowError(
                f"Provider is not available: {self.provider_status['message']}"
            )

        try:
            decision = self.router.select(runtime.spec)
        except ProviderRoutingError as exc:
            raise WorkflowError(
                f"Provider is not available: {self.provider_status['message']}"
            ) from exc
        self._invalidate_workflow_from(
            project,
            "video",
            reason="shot_generation_submitted",
            actor="generation-router",
        )
        policy_report = self.policy.assess_shot(
            runtime.shot,
            runtime.spec,
            revision=runtime.revision,
        ).as_dict()
        project.policy_reports.append(policy_report)
        if not policy_report["passed"]:
            self._record_event(
                project,
                action="policy.shot_blocked",
                actor="policy-engine",
                message=f"{shot_id} failed policy checks.",
                shot_id=shot_id,
                details=policy_report,
            )
            self._persist()
            raise PolicyViolation(
                f"content policy blocked {shot_id}",
                policy_report,
            )
        projected_spend = self._cost_report(project)["spent"] + decision.estimated_cost
        if projected_spend > project.brief.budget:
            raise WorkflowError(
                "project budget exceeded: "
                f"${projected_spend:.2f} > ${project.brief.budget:.2f}"
            )
        self._enforce_job_quota(project.brief.tenant_id)
        job = self.jobs.create(
            runtime.spec,
            f"{project_id}:{shot_id}:revision:{runtime.revision}",
            trace_id=project.trace_id,
        )
        runtime.current_job_id = job.job_id
        project.status = ProjectStatus.IN_PROGRESS
        if job.status == JobStatus.CREATED:
            self.jobs.transition(job.job_id, JobStatus.VALIDATED)
            self.jobs.transition(job.job_id, JobStatus.QUEUED)
            self.jobs.transition(job.job_id, JobStatus.ADMITTED)
            self.jobs.transition(job.job_id, JobStatus.RUNNING)
            self._record_event(
                project,
                action="shot.submitted",
                actor="generation-router",
                message=f"{shot_id} submitted to {decision.provider.name}.",
                shot_id=shot_id,
                details={
                    "job_id": job.job_id,
                    "provider": decision.provider.name,
                    "estimated_cost": decision.estimated_cost,
                    "revision": runtime.revision,
                },
            )
            self._persist()
        elif job.status == JobStatus.VALIDATED:
            self.jobs.transition(job.job_id, JobStatus.QUEUED)
            self.jobs.transition(job.job_id, JobStatus.ADMITTED)
            self.jobs.transition(job.job_id, JobStatus.RUNNING)
            self._record_event(
                project,
                action="job.process_started",
                actor="generation-worker",
                message=f"{shot_id} queued job started.",
                shot_id=shot_id,
                details={
                    "job_id": job.job_id,
                    "provider": decision.provider.name,
                    "estimated_cost": decision.estimated_cost,
                    "revision": runtime.revision,
                },
            )
            self._persist()
        elif job.status == JobStatus.QUEUED:
            self.jobs.transition(job.job_id, JobStatus.ADMITTED)
            self.jobs.transition(job.job_id, JobStatus.RUNNING)
            self._record_event(
                project,
                action="job.process_started",
                actor="generation-worker",
                message=f"{shot_id} queued job started.",
                shot_id=shot_id,
                details={
                    "job_id": job.job_id,
                    "provider": decision.provider.name,
                    "estimated_cost": decision.estimated_cost,
                    "revision": runtime.revision,
                },
            )
            self._persist()
        elif job.status == JobStatus.ADMITTED:
            self.jobs.transition(job.job_id, JobStatus.RUNNING)
            self._record_event(
                project,
                action="job.process_started",
                actor="generation-worker",
                message=f"{shot_id} admitted job started.",
                shot_id=shot_id,
                details={
                    "job_id": job.job_id,
                    "provider": decision.provider.name,
                    "estimated_cost": decision.estimated_cost,
                    "revision": runtime.revision,
                },
            )
            self._persist()
        elif job.status in {
            JobStatus.FAILED,
            JobStatus.QUALITY_REJECTED,
            JobStatus.RETRY_WAIT,
        }:
            if job.attempts >= job.max_attempts:
                raise WorkflowError(
                    f"retry limit reached for {shot_id}: "
                    f"{job.attempts}/{job.max_attempts}"
                )
            previous_status = job.status
            if job.status in {
                JobStatus.FAILED,
                JobStatus.QUALITY_REJECTED,
            }:
                self.jobs.transition(
                    job.job_id,
                    JobStatus.RETRY_WAIT,
                    reason="manual retry requested",
                )
            self.jobs.transition(job.job_id, JobStatus.QUEUED)
            self.jobs.transition(job.job_id, JobStatus.ADMITTED)
            self.jobs.transition(job.job_id, JobStatus.RUNNING)
            self._record_event(
                project,
                action="shot.retry_started",
                actor="generation-router",
                message=f"{shot_id} retry started from {previous_status}.",
                shot_id=shot_id,
                details={
                    "job_id": job.job_id,
                    "previous_status": previous_status,
                    "provider": decision.provider.name,
                    "revision": runtime.revision,
                },
            )
            self._persist()
        elif job.status == JobStatus.RUNNING:
            raise WorkflowError(f"shot job is already running: {job.job_id}")
        elif job.status == JobStatus.SUCCEEDED and runtime.current_artifact is None:
            raise WorkflowError(f"shot job completed without an artifact: {job.job_id}")
        elif job.status == JobStatus.CANCELED:
            raise WorkflowError(f"shot job was canceled: {job.job_id}")

        route_attempts: list[dict[str, Any]] = []
        attempted_providers: set[str] = set()
        while True:
            route_attempts.append(
                {
                    "provider": decision.provider.name,
                    "estimated_cost": decision.estimated_cost,
                    "reason": decision.reason,
                }
            )
            attempted_providers.add(decision.provider.name)
            try:
                artifact = self._generate_artifact(
                    decision.provider,
                    runtime.spec,
                    job_id=job.job_id,
                    output_dir=self.output_root / project_id / "shots",
                    estimated_cost=decision.estimated_cost,
                    tenant_id=project.brief.tenant_id,
                    project_id=project_id,
                )
                quality_result = self._probe_artifact_media(artifact)
                quality = self._evaluate_artifact_quality(
                    artifact,
                    quality_result,
                    runtime.spec,
                )
                if not quality["passed"]:
                    self.jobs.transition(
                        job.job_id,
                        JobStatus.QUALITY_REJECTED,
                        reason=quality_result.error or "quality gate failed",
                    )
                    runtime.quality = quality
                    self._record_event(
                        project,
                        action="shot.quality_rejected",
                        actor="quality-gate",
                        message=f"{shot_id} failed the media quality gate.",
                        shot_id=shot_id,
                        details={"job_id": job.job_id, "quality": quality},
                    )
                    self._persist()
                    raise WorkflowError(f"quality gate failed for {shot_id}")

                job.add_artifact(artifact)
                self.jobs.transition(job.job_id, JobStatus.SUCCEEDED)
                runtime.current_artifact = artifact.model_dump(mode="json")
                runtime.route = {
                    "provider": decision.provider.name,
                    "estimated_cost": decision.estimated_cost,
                    "reason": decision.reason,
                    "attempts": copy.deepcopy(route_attempts),
                }
                runtime.quality = quality
                runtime.review_status = ReviewStatus.PENDING
                runtime.artifact_history.append(
                    {
                        "revision": runtime.revision,
                        "job_id": job.job_id,
                        "artifact": runtime.current_artifact,
                        "route": runtime.route,
                        "quality": runtime.quality,
                        "created_at": datetime.now(timezone.utc).isoformat(),
                    }
                )
                self._record_event(
                    project,
                    action="shot.generated",
                    actor=decision.provider.name,
                    message=f"{shot_id} generated and passed quality checks.",
                    shot_id=shot_id,
                    details={
                        "job_id": job.job_id,
                        "artifact_id": artifact.artifact_id,
                        "estimated_cost": decision.estimated_cost,
                        "revision": runtime.revision,
                        "provider_attempts": copy.deepcopy(route_attempts),
                    },
                )
                self._persist()
                return {
                    **self.shot_view(runtime),
                }
            except WorkflowError:
                raise
            except Exception as exc:
                try:
                    fallback = self.router.select(
                        runtime.spec,
                        exclude_provider_names=attempted_providers,
                    )
                except ProviderRoutingError:
                    fallback = None
                if fallback is not None:
                    projected_spend = (
                        self._cost_report(project)["spent"]
                        + sum(
                            float(item["estimated_cost"])
                            for item in route_attempts
                        )
                        + fallback.estimated_cost
                    )
                    if projected_spend <= project.brief.budget:
                        self._record_event(
                            project,
                            action="shot.provider_failover",
                            actor="generation-router",
                            message=(
                                f"{shot_id} failed on {decision.provider.name}; "
                                f"failing over to {fallback.provider.name}."
                            ),
                            shot_id=shot_id,
                            details={
                                "job_id": job.job_id,
                                "from_provider": decision.provider.name,
                                "to_provider": fallback.provider.name,
                                "error": str(exc),
                                "provider_attempts": copy.deepcopy(route_attempts),
                            },
                        )
                        self._persist()
                        decision = fallback
                        continue

                if job.status == JobStatus.RUNNING:
                    self.jobs.transition(job.job_id, JobStatus.FAILED, reason=str(exc))
                    self._schedule_retry_after_failure(
                        project,
                        job,
                        error=str(exc),
                        actor=decision.provider.name,
                    )
                    self._record_event(
                        project,
                        action="shot.failed",
                        actor=decision.provider.name,
                        message=f"{shot_id} generation failed.",
                        shot_id=shot_id,
                        details={
                            "job_id": job.job_id,
                            "error": str(exc),
                            "provider_attempts": copy.deepcopy(route_attempts),
                        },
                    )
                    self._persist()
                raise WorkflowError(f"generation failed for {shot_id}: {exc}") from exc

    def recover_stale_jobs(
        self,
        project_id: str | None = None,
        *,
        stale_after_seconds: float | None = None,
        actor: str = "operations",
    ) -> dict[str, Any]:
        """Recover provider jobs left active by a crashed process."""
        if project_id is not None:
            self._project(project_id)
        try:
            lease_seconds = (
                self.job_lease_policy.stale_after_seconds
                if stale_after_seconds is None
                else float(stale_after_seconds)
            )
        except (TypeError, ValueError) as exc:
            raise WorkflowError("stale_after_seconds must be a number") from exc
        if lease_seconds <= 0:
            raise WorkflowError("stale_after_seconds must be > 0")
        return self._recover_stale_jobs(
            project_id=project_id,
            stale_after_seconds=lease_seconds,
            actor=actor,
        )

    def _recover_stale_jobs(
        self,
        *,
        project_id: str | None = None,
        stale_after_seconds: float | None = None,
        actor: str,
    ) -> dict[str, Any]:
        lease_seconds = (
            self.job_lease_policy.stale_after_seconds
            if stale_after_seconds is None
            else stale_after_seconds
        )
        now = datetime.now(timezone.utc)
        recovered: list[dict[str, Any]] = []
        skipped: list[dict[str, Any]] = []
        for job in self.jobs.all():
            if project_id is not None and job.spec.project_id != project_id:
                continue
            if job.status not in {JobStatus.ADMITTED, JobStatus.RUNNING}:
                continue
            project = self.projects.get(job.spec.project_id)
            if project is None:
                skipped.append(
                    {
                        "job_id": job.job_id,
                        "project_id": job.spec.project_id,
                        "reason": "project is missing",
                    }
                )
                continue
            if project.archived_at is not None:
                skipped.append(
                    {
                        "job_id": job.job_id,
                        "project_id": job.spec.project_id,
                        "reason": "project is archived",
                    }
                )
                continue
            last_event_at = job.events[-1].occurred_at if job.events else now
            if last_event_at.tzinfo is None:
                last_event_at = last_event_at.replace(tzinfo=timezone.utc)
            if job.last_heartbeat_at is not None:
                last_event_at = max(last_event_at, job.last_heartbeat_at)
            age_seconds = max((now - last_event_at).total_seconds(), 0.0)
            if job.lease_expires_at is not None and stale_after_seconds is None:
                if job.lease_expires_at > now:
                    continue
            elif age_seconds < lease_seconds:
                continue
            reason = (
                f"execution lease expired after {age_seconds:.1f}s "
                f"(limit {lease_seconds:.1f}s)"
            )
            previous_status = job.status
            job.transition(JobStatus.FAILED, reason=reason)
            self._schedule_retry_after_failure(
                project,
                job,
                error=reason,
                actor=actor,
            )
            self._release_job_lease(job)
            self._record_event(
                project,
                action="job.stale_recovered",
                actor=actor,
                message=f"{job.spec.shot_id} stale execution was recovered.",
                shot_id=job.spec.shot_id,
                details={
                    "job_id": job.job_id,
                    "previous_status": previous_status,
                    "age_seconds": round(age_seconds, 2),
                    "lease_seconds": lease_seconds,
                    "retry_at": job.retry_at.isoformat() if job.retry_at else None,
                },
            )
            recovered.append(
                {
                    "job_id": job.job_id,
                    "project_id": job.spec.project_id,
                    "shot_id": job.spec.shot_id,
                    "previous_status": previous_status,
                    "status": job.status,
                    "attempts": job.attempts,
                    "retry_at": job.retry_at.isoformat() if job.retry_at else None,
                }
            )
        if recovered:
            self._persist()
        return {
            "project_id": project_id,
            "lease_seconds": lease_seconds,
            "recovered": recovered,
            "skipped": skipped,
            "recovered_count": len(recovered),
        }

    def retry_shot(self, project_id: str, shot_id: str) -> dict[str, Any]:
        project = self._project(project_id)
        self._ensure_active(project)
        runtime = self._shot(project, shot_id)
        if not runtime.current_job_id:
            raise WorkflowError("shot has no failed job to retry")
        job = self.jobs.get(runtime.current_job_id)
        if job.status not in {
            JobStatus.FAILED,
            JobStatus.QUALITY_REJECTED,
            JobStatus.RETRY_WAIT,
        }:
            raise WorkflowError(
                f"shot job is not retryable: {job.status}"
            )
        self._record_event(
            project,
            action="shot.retry_requested",
            actor="studio-user",
            message=f"Retry requested for {shot_id}.",
            shot_id=shot_id,
            details={"job_id": job.job_id, "status": job.status},
        )
        self._persist()
        return self.submit_shot(project_id, shot_id)

    def _schedule_retry_after_failure(
        self,
        project: ProjectRuntime,
        job: JobRecord,
        *,
        error: str,
        actor: str,
    ) -> None:
        if job.attempts >= job.max_attempts:
            self._record_event(
                project,
                action="job.retry_exhausted",
                actor=actor,
                message=f"{job.spec.shot_id} reached its retry limit.",
                shot_id=job.spec.shot_id,
                details={
                    "job_id": job.job_id,
                    "attempts": job.attempts,
                    "max_attempts": job.max_attempts,
                    "error": error,
                },
            )
            return
        delay = self.retry_policy.delay_for_attempt(job.attempts)
        retry_at = datetime.now(timezone.utc) + timedelta(seconds=delay)
        self.jobs.schedule_retry(
            job.job_id,
            retry_at=retry_at,
            reason=error,
        )
        self._record_event(
            project,
            action="job.retry_scheduled",
            actor=actor,
            message=f"{job.spec.shot_id} will retry after backoff.",
            shot_id=job.spec.shot_id,
            details={
                "job_id": job.job_id,
                "retry_at": retry_at.isoformat(),
                "delay_seconds": delay,
                "attempts": job.attempts,
                "max_attempts": job.max_attempts,
                "error": error,
            },
        )

    def schedule_retry(
        self,
        project_id: str,
        shot_id: str,
        *,
        delay_seconds: float | None = None,
        actor: str = "generation-worker",
    ) -> dict[str, Any]:
        project = self._project(project_id)
        self._ensure_active(project)
        runtime = self._shot(project, shot_id)
        if not runtime.current_job_id:
            raise WorkflowError("shot has no failed job to retry")
        job = self.jobs.get(runtime.current_job_id)
        if job.status not in {
            JobStatus.FAILED,
            JobStatus.QUALITY_REJECTED,
            JobStatus.RETRY_WAIT,
        }:
            raise WorkflowError(
                f"shot job is not retryable: {job.status}"
            )
        if job.attempts >= job.max_attempts:
            raise WorkflowError(
                f"retry limit reached for {shot_id}: "
                f"{job.attempts}/{job.max_attempts}"
            )
        delay = (
            self.retry_policy.delay_for_attempt(job.attempts)
            if delay_seconds is None
            else delay_seconds
        )
        if delay < 0:
            raise WorkflowError("retry delay must be >= 0")
        retry_at = datetime.now(timezone.utc) + timedelta(seconds=delay)
        self.jobs.schedule_retry(
            job.job_id,
            retry_at=retry_at,
            reason=f"retry scheduled after {delay:.2f}s",
        )
        self._record_event(
            project,
            action="shot.retry_scheduled",
            actor=actor,
            message=f"Retry scheduled for {shot_id}.",
            shot_id=shot_id,
            details={
                "job_id": job.job_id,
                "retry_at": retry_at.isoformat(),
                "delay_seconds": delay,
                "attempts": job.attempts,
                "max_attempts": job.max_attempts,
                "last_error": job.last_error,
            },
        )
        self._persist()
        return self.shot_view(runtime)

    def _notify_job_queue(self, job: JobRecord) -> None:
        if (
            os.getenv("MEDIAFORGE_QUEUE_BACKEND", "sqlite").strip().lower() == "redis"
            and job.status in {JobStatus.VALIDATED, JobStatus.QUEUED}
        ):
            try:
                self.enterprise.redis_queue.enqueue(self._job_view(job))
            except EnterpriseConfigurationError as exc:
                raise WorkflowError(str(exc)) from exc

    def enqueue_shot(self, project_id: str, shot_id: str) -> dict[str, Any]:
        with self.planning.mutation_guard(project_id):
            return self._enqueue_shot(project_id, shot_id)

    def _enqueue_shot(self, project_id: str, shot_id: str) -> dict[str, Any]:
        if self.planning.has_active(project_id):
            raise PlanningError('approve or cancel the active planning draft first')
        project = self._project(project_id)
        self._ensure_active(project)
        runtime = self._shot(project, shot_id)
        if runtime.current_artifact and runtime.review_status != ReviewStatus.CHANGES_REQUESTED:
            return self.shot_view(runtime)
        if runtime.current_artifact and runtime.review_status == ReviewStatus.CHANGES_REQUESTED:
            raise WorkflowError("run a revision before enqueueing this shot")
        if project.status == ProjectStatus.DRAFT:
            raise WorkflowError("generate a plan before enqueueing shots")
        if not self.provider_status.get("configured", False):
            raise WorkflowError(
                f"Provider is not available: {self.provider_status['message']}"
            )
        if runtime.current_job_id:
            try:
                existing_job = self.jobs.get(runtime.current_job_id)
            except KeyError:
                existing_job = None
            if existing_job and existing_job.status in {
                JobStatus.VALIDATED,
                JobStatus.QUEUED,
                JobStatus.ADMITTED,
                JobStatus.RETRY_WAIT,
            }:
                self._notify_job_queue(existing_job)
                return self.shot_view(runtime)
            if existing_job and existing_job.status == JobStatus.RUNNING:
                raise WorkflowError(f"shot job is already running: {existing_job.job_id}")
            if existing_job and existing_job.status == JobStatus.CANCELED:
                raise WorkflowError(
                    f"shot job was canceled; create a revision before enqueueing: "
                    f"{existing_job.job_id}"
                )

        try:
            decision = self.router.select(runtime.spec)
        except ProviderRoutingError as exc:
            raise WorkflowError(
                f"Provider is not available: {self.provider_status['message']}"
            ) from exc
        policy_report = self.policy.assess_shot(
            runtime.shot,
            runtime.spec,
            revision=runtime.revision,
        ).as_dict()
        project.policy_reports.append(policy_report)
        if not policy_report["passed"]:
            self._record_event(
                project,
                action="policy.shot_blocked",
                actor="policy-engine",
                message=f"{shot_id} failed policy checks before enqueue.",
                shot_id=shot_id,
                details=policy_report,
            )
            self._persist()
            raise PolicyViolation(
                f"content policy blocked {shot_id}",
                policy_report,
            )

        projected_spend = (
            self._cost_report(project)["spent"]
            + self._reserved_generation_cost(project)
            + decision.estimated_cost
        )
        if projected_spend > project.brief.budget:
            raise WorkflowError(
                "project budget exceeded: "
                f"${projected_spend:.2f} > ${project.brief.budget:.2f}"
            )
        self._enforce_job_quota(project.brief.tenant_id)
        job = self.jobs.create(
            runtime.spec,
            f"{project_id}:{shot_id}:revision:{runtime.revision}",
            trace_id=project.trace_id,
        )
        runtime.current_job_id = job.job_id
        project.status = ProjectStatus.IN_PROGRESS
        if job.status == JobStatus.CREATED:
            self.jobs.transition(job.job_id, JobStatus.VALIDATED)
            self.jobs.transition(job.job_id, JobStatus.QUEUED)
        elif job.status == JobStatus.VALIDATED:
            self.jobs.transition(job.job_id, JobStatus.QUEUED)
        elif job.status != JobStatus.QUEUED:
            raise WorkflowError(f"shot job cannot be enqueued from {job.status}")
        self._record_event(
            project,
            action="shot.enqueued",
            actor="generation-router",
            message=f"{shot_id} queued for {decision.provider.name}.",
            shot_id=shot_id,
            details={
                "job_id": job.job_id,
                "provider": decision.provider.name,
                "estimated_cost": decision.estimated_cost,
                "revision": runtime.revision,
            },
        )
        self._persist()
        self._notify_job_queue(job)
        return self.shot_view(runtime)

    def enqueue_pending_shots(self, project_id: str) -> dict[str, Any]:
        project = self._project(project_id)
        self._ensure_active(project)
        if not project.shots:
            raise WorkflowError("generate a plan before enqueueing shots")

        queued = []
        skipped = []
        for shot_id, runtime in project.shots.items():
            if runtime.current_artifact:
                skipped.append(shot_id)
                continue
            if runtime.review_status == ReviewStatus.CHANGES_REQUESTED:
                skipped.append(shot_id)
                continue
            if runtime.current_job_id:
                try:
                    job = self.jobs.get(runtime.current_job_id)
                except KeyError:
                    job = None
                if job and job.status in {
                    JobStatus.VALIDATED,
                    JobStatus.QUEUED,
                    JobStatus.ADMITTED,
                    JobStatus.RUNNING,
                    JobStatus.RETRY_WAIT,
                }:
                    skipped.append(shot_id)
                    continue
            queued.append(self.enqueue_shot(project_id, shot_id))

        self._record_event(
            project,
            action="project.batch_enqueued",
            actor="studio-user",
            message=f"{len(queued)} shot(s) enqueued in batch.",
            details={"queued": len(queued), "skipped": skipped},
        )
        self._persist()
        return {
            "project_id": project_id,
            "queued": len(queued),
            "skipped": skipped,
            "project": self.project_view(project_id),
        }

    def process_job(
        self,
        project_id: str,
        job_id: str,
        *,
        actor: str = "generation-worker",
        worker_id: str | None = None,
    ) -> dict[str, Any]:
        project = self._project(project_id)
        self._ensure_active(project)
        try:
            job = self.jobs.get(job_id)
        except KeyError as exc:
            raise JobNotFound(job_id) from exc
        if job.spec.project_id != project_id:
            raise JobNotFound(job_id)
        if job.status not in {
            JobStatus.VALIDATED,
            JobStatus.QUEUED,
            JobStatus.ADMITTED,
        }:
            raise WorkflowError(f"job is not queued for processing: {job.status}")
        runtime = self._shot(project, job.spec.shot_id)
        if runtime.current_job_id != job_id:
            raise WorkflowError(
                f"job is not the current job for {job.spec.shot_id}: {job_id}"
            )
        if job.worker_id:
            if not worker_id:
                raise WorkflowError("job is leased; worker_id is required to process it")
            self._assert_worker_lease(job, worker_id)
        elif worker_id:
            raise WorkflowError("job is not leased to a worker")
        self._record_event(
            project,
            action="job.process_requested",
            actor=actor,
            message=f"{job.spec.shot_id} queued job requested for processing.",
            shot_id=job.spec.shot_id,
            details={"job_id": job_id, "status": job.status},
        )
        self._persist()
        result: dict[str, Any]
        try:
            result = self.submit_shot(project_id, job.spec.shot_id)
        finally:
            self._release_job_lease(job)
            self._persist()
        result["job"] = self._job_view(job)
        return result

    def provider_callback(
        self,
        project_id: str,
        job_id: str,
        *,
        event_id: str,
        provider: str,
        status: JobStatus,
        reason: str | None = None,
        artifact_uri: str | None = None,
        artifact_kind: str = "video",
        mime_type: str | None = None,
        estimated_cost: float | None = None,
        actual_cost: float | None = None,
        actor: str = "provider-callback",
        worker_id: str | None = None,
        external_reference: str | None = None,
    ) -> dict[str, Any]:
        """Reconcile an asynchronous provider event exactly once."""
        project = self._project(project_id)
        self._ensure_active(project)
        try:
            job = self.jobs.get(job_id)
        except KeyError as exc:
            raise JobNotFound(job_id) from exc
        if job.spec.project_id != project_id:
            raise JobNotFound(job_id)
        clean_event_id = event_id.strip()
        clean_provider = provider.strip()
        if not clean_event_id or not clean_provider:
            raise WorkflowError("provider callback event_id and provider are required")
        if any(cost is not None and (not math.isfinite(cost) or cost < 0) for cost in (estimated_cost, actual_cost)):
            raise WorkflowError("provider callback costs must be finite and non-negative")
        clean_external_reference = (
            str(external_reference).strip() if external_reference is not None else None
        )
        if clean_external_reference and len(clean_external_reference) > 240:
            raise WorkflowError("provider callback external_reference is too long")

        duplicate = self._provider_callback_event(
            project,
            job_id=job_id,
            event_id=clean_event_id,
        )
        if duplicate is not None:
            return {
                "project_id": project_id,
                "job_id": job_id,
                "event_id": clean_event_id,
                "idempotent": True,
                "job": self._job_view(job),
            }

        if worker_id:
            self._assert_worker_lease(job, worker_id)

        expected_provider = next(
            (
                str(event.details.get("provider"))
                for event in reversed(project.audit_events)
                if event.details.get("job_id") == job_id
                and event.details.get("provider")
            ),
            None,
        )
        if expected_provider and expected_provider != clean_provider:
            raise WorkflowError(
                f"provider callback does not match routed provider: {expected_provider}"
            )

        if status not in {
            JobStatus.RUNNING,
            JobStatus.SUCCEEDED,
            JobStatus.FAILED,
            JobStatus.CANCELED,
        }:
            raise WorkflowError(
                f"unsupported provider callback status: {status.value}"
            )
        callback_artifact = None
        if status == JobStatus.SUCCEEDED:
            callback_artifact = self._artifact_from_provider_callback(
                project_id,
                job,
                artifact_uri=artifact_uri,
                artifact_kind=artifact_kind,
                mime_type=mime_type,
                provider=clean_provider,
            )

        self._advance_callback_job_to_running(job)
        runtime = self._shot(project, job.spec.shot_id)
        callback_details = {
            "job_id": job_id,
            "callback_event_id": clean_event_id,
            "provider": clean_provider,
            "status": status.value,
            "reason": reason,
            "estimated_cost": estimated_cost,
            "actual_cost": actual_cost,
            "worker_id": worker_id,
            "external_reference": clean_external_reference,
        }

        if status == JobStatus.RUNNING:
            self._record_event(
                project,
                action="job.provider_callback_received",
                actor=actor,
                message=f"{job.spec.shot_id} provider callback received.",
                shot_id=job.spec.shot_id,
                details=callback_details,
            )
        elif status == JobStatus.FAILED:
            failure_reason = reason or "provider callback reported failure"
            self.jobs.transition(job_id, JobStatus.FAILED, reason=failure_reason)
            self._schedule_retry_after_failure(
                project,
                job,
                error=failure_reason,
                actor=clean_provider,
            )
            self._record_event(
                project,
                action="job.provider_callback_received",
                actor=actor,
                message=f"{job.spec.shot_id} provider callback reported failure.",
                shot_id=job.spec.shot_id,
                details=callback_details | {
                    "retry_at": job.retry_at.isoformat() if job.retry_at else None,
                },
            )
        elif status == JobStatus.CANCELED:
            self.jobs.transition(
                job_id,
                JobStatus.CANCELED,
                reason=reason or "provider callback canceled",
            )
            self._record_event(
                project,
                action="job.provider_callback_received",
                actor=actor,
                message=f"{job.spec.shot_id} provider callback canceled the job.",
                shot_id=job.spec.shot_id,
                details=callback_details,
            )
        elif status == JobStatus.SUCCEEDED:
            artifact = callback_artifact
            if artifact is None:
                raise WorkflowError("successful provider callback artifact is missing")
            self.enterprise.billing.record(
                event_id=f"generation:{job_id}:{job.attempts}",
                tenant_id=project.brief.tenant_id,
                project_id=project_id,
                category="provider_generation",
                quantity=1,
                unit_price=actual_cost if actual_cost is not None else (estimated_cost or 0.0),
                metadata={"provider": clean_provider, "artifact_id": artifact.artifact_id, "callback_event_id": clean_event_id, "cost_basis": "actual" if actual_cost is not None else "estimate"},
            )
            quality_result = self._probe_artifact_media(artifact)
            quality = self._evaluate_artifact_quality(
                artifact,
                quality_result,
                runtime.spec,
            )
            if not quality["passed"]:
                self.jobs.transition(
                    job_id,
                    JobStatus.QUALITY_REJECTED,
                    reason=quality_result.error or "quality gate failed",
                )
                runtime.quality = quality
                self._record_event(
                    project,
                    action="job.provider_callback_received",
                    actor=actor,
                    message=f"{job.spec.shot_id} provider callback failed quality checks.",
                    shot_id=job.spec.shot_id,
                    details=callback_details | {"quality": quality},
                )
                self._schedule_retry_after_failure(
                    project,
                    job,
                    error=quality_result.error or "quality gate failed",
                    actor="quality-gate",
                )
            else:
                job.add_artifact(artifact)
                self.jobs.transition(job_id, JobStatus.SUCCEEDED)
                runtime.current_artifact = artifact.model_dump(mode="json")
                runtime.route = {
                    "provider": clean_provider,
                    "estimated_cost": estimated_cost or 0.0,
                    "actual_cost": actual_cost,
                    "reason": "provider callback",
                }
                runtime.quality = quality
                runtime.review_status = ReviewStatus.PENDING
                runtime.artifact_history.append(
                    {
                        "revision": runtime.revision,
                        "job_id": job_id,
                        "artifact": runtime.current_artifact,
                        "route": runtime.route,
                        "quality": runtime.quality,
                        "created_at": datetime.now(timezone.utc).isoformat(),
                    }
                )
                callback_details["quality_passed"] = True
                callback_details["artifact_id"] = artifact.artifact_id
                self._record_event(
                    project,
                    action="job.provider_callback_received",
                    actor=actor,
                    message=f"{job.spec.shot_id} provider callback completed.",
                    shot_id=job.spec.shot_id,
                    details=callback_details,
                )
                self._record_event(
                    project,
                    action="shot.generated",
                    actor=clean_provider,
                    message=f"{job.spec.shot_id} generated from provider callback.",
                    shot_id=job.spec.shot_id,
                    details=callback_details,
                )
        if status in {
            JobStatus.SUCCEEDED,
            JobStatus.FAILED,
            JobStatus.CANCELED,
        }:
            self._release_job_lease(job)
        elif job.worker_id:
            now = datetime.now(timezone.utc)
            job.last_heartbeat_at = now
            job.lease_expires_at = now + timedelta(
                seconds=self.job_lease_policy.stale_after_seconds
            )
        self._persist()
        result = {
            "project_id": project_id,
            "job_id": job_id,
            "event_id": clean_event_id,
            "idempotent": False,
            "job": self._job_view(job),
        }
        if status == JobStatus.SUCCEEDED and runtime.current_artifact:
            result["shot"] = self.shot_view(runtime)
        return result

    def reconcile_replicate_webhook(
        self,
        project_id: str,
        job_id: str,
        *,
        webhook_id: str,
        prediction: dict[str, Any],
    ) -> dict[str, Any]:
        """Reconcile one verified native Replicate delivery into a Job."""
        project = self._project(project_id)
        self._ensure_active(project)
        try:
            job = self.jobs.get(job_id)
        except KeyError as exc:
            raise JobNotFound(job_id) from exc
        if job.spec.project_id != project_id:
            raise JobNotFound(job_id)
        clean_webhook_id = webhook_id.strip()
        prediction_id = str(prediction.get("id") or "").strip()
        provider_status = str(prediction.get("status") or "").strip().lower()
        if not clean_webhook_id or not prediction_id or not provider_status:
            raise WorkflowError(
                "Replicate webhook requires id, status, and webhook identifier"
            )
        expected_prediction_id = self._replicate_prediction_for_job(project, job_id)
        if expected_prediction_id != prediction_id:
            raise WorkflowError("Replicate webhook prediction does not match the job")
        event_id = f"replicate:{clean_webhook_id}"
        duplicate = self._provider_callback_event(
            project,
            job_id=job_id,
            event_id=event_id,
        )
        if duplicate is not None:
            return {
                "project_id": project_id,
                "job_id": job_id,
                "event_id": event_id,
                "idempotent": True,
                "job": self._job_view(job),
            }
        if job.status in {
            JobStatus.SUCCEEDED,
            JobStatus.FAILED,
            JobStatus.CANCELED,
            JobStatus.QUALITY_REJECTED,
        }:
            return {
                "project_id": project_id,
                "job_id": job_id,
                "event_id": event_id,
                "ignored": True,
                "reason": f"job is already terminal: {job.status.value}",
                "job": self._job_view(job),
            }

        status_mapping = {
            "starting": JobStatus.RUNNING,
            "processing": JobStatus.RUNNING,
            "succeeded": JobStatus.SUCCEEDED,
            "failed": JobStatus.FAILED,
            "canceled": JobStatus.CANCELED,
        }
        callback_status = status_mapping.get(provider_status)
        if callback_status is None:
            raise WorkflowError(
                f"unsupported Replicate webhook status: {provider_status}"
            )
        reason = str(prediction.get("error") or "").strip() or None
        artifact_uri = None
        artifact_kind = "video"
        mime_type = None
        estimated_cost = None
        if callback_status == JobStatus.SUCCEEDED:
            provider = self._provider_named("replicate-video")
            archive = getattr(provider, "archive_webhook_prediction", None)
            if not callable(archive):
                raise WorkflowError("Replicate webhook archiver is not configured")
            try:
                artifact = archive(
                    prediction,
                    spec=job.spec,
                    job_id=job_id,
                    output_dir=self.output_root / project_id / "shots",
                )
            except Exception as exc:
                callback_status = JobStatus.FAILED
                reason = f"failed to archive Replicate webhook output: {exc}"
            else:
                artifact_uri = artifact.uri
                artifact_kind = artifact.kind
                mime_type = artifact.mime_type
                estimated_cost = provider.estimate_cost(job.spec)
        result = self.provider_callback(
            project_id,
            job_id,
            event_id=event_id,
            provider="replicate-video",
            status=callback_status,
            reason=reason,
            artifact_uri=artifact_uri,
            artifact_kind=artifact_kind,
            mime_type=mime_type,
            estimated_cost=estimated_cost,
            actor="replicate-webhook",
            external_reference=prediction_id,
        )
        result["replicate"] = {
            "prediction_id": prediction_id,
            "status": provider_status,
            "webhook_id": clean_webhook_id,
        }
        return result

    @staticmethod
    def _provider_callback_event(
        project: ProjectRuntime,
        *,
        job_id: str,
        event_id: str,
    ) -> AuditEvent | None:
        return next(
            (
                event
                for event in project.audit_events
                if event.action == "job.provider_callback_received"
                and event.details.get("job_id") == job_id
                and event.details.get("callback_event_id") == event_id
            ),
            None,
        )

    @staticmethod
    def _replicate_prediction_for_job(
        project: ProjectRuntime,
        job_id: str,
    ) -> str:
        for event in reversed(project.audit_events):
            if (
                event.action == "job.provider_callback_received"
                and event.details.get("job_id") == job_id
                and event.details.get("provider") == "replicate-video"
            ):
                reference = str(event.details.get("external_reference") or "").strip()
                if reference:
                    return reference
        raise WorkflowError("Replicate prediction is not registered for this job")

    def _provider_named(self, name: str) -> GenerationProvider:
        for registration in self.router.registrations:
            if registration.provider.name == name:
                return registration.provider
        raise WorkflowError(f"Provider is not registered: {name}")

    @staticmethod
    def _advance_callback_job_to_running(job: JobRecord) -> None:
        transitions = {
            JobStatus.CREATED: [JobStatus.VALIDATED, JobStatus.QUEUED, JobStatus.ADMITTED, JobStatus.RUNNING],
            JobStatus.VALIDATED: [JobStatus.QUEUED, JobStatus.ADMITTED, JobStatus.RUNNING],
            JobStatus.QUEUED: [JobStatus.ADMITTED, JobStatus.RUNNING],
            JobStatus.ADMITTED: [JobStatus.RUNNING],
        }
        for next_status in transitions.get(job.status, []):
            job.transition(next_status)
        if job.status != JobStatus.RUNNING:
            raise WorkflowError(
                f"provider callback cannot reconcile job in state: {job.status}"
            )

    @staticmethod
    def _routed_provider_for_job(project: ProjectRuntime, job_id: str) -> str | None:
        """Return the provider selected at enqueue time for a leased Worker."""
        for event in reversed(project.audit_events):
            if event.details.get("job_id") != job_id:
                continue
            provider = str(event.details.get("provider") or "").strip()
            if provider:
                return provider
        return None

    def _artifact_from_provider_callback(
        self,
        project_id: str,
        job: JobRecord,
        *,
        artifact_uri: str | None,
        artifact_kind: str,
        mime_type: str | None,
        provider: str,
    ) -> Artifact:
        if artifact_kind not in {"image", "video"}:
            raise WorkflowError("provider callback artifact kind must be image or video")
        if not artifact_uri:
            raise WorkflowError("successful provider callback requires artifact_uri")
        try:
            path = Path(artifact_uri).resolve()
            output_root = self.output_root.resolve()
        except OSError as exc:
            raise WorkflowError("provider callback artifact path is invalid") from exc
        project_root = (output_root / project_id).resolve()
        if project_root != path and project_root not in path.parents:
            raise WorkflowError(
                "provider callback artifact must be inside the project artifact root"
            )
        if not path.is_file():
            raise WorkflowError("provider callback artifact file does not exist")
        metadata_uri = self._write_legacy_metadata_sidecar(
            path,
            provider=provider,
            job_id=job.job_id,
            spec=job.spec.model_dump(mode="json"),
        )
        return Artifact(
            artifact_id=f"artifact_{uuid4().hex[:12]}",
            job_id=job.job_id,
            kind=artifact_kind,
            uri=str(path),
            mime_type=mime_type or mimetypes.guess_type(path.name)[0] or (
                "image/png" if artifact_kind == "image" else "video/mp4"
            ),
            sha256=sha256_file(path),
            size_bytes=path.stat().st_size,
            duration_seconds=None,
            metadata_uri=metadata_uri,
            created_at=datetime.now(timezone.utc),
        )

    def _promote_due_retries(
        self,
        project_id: str,
        *,
        actor: str,
        limit: int | None = None,
    ) -> list[JobRecord]:
        project = self._project(project_id)
        if project.archived_at is not None:
            return []
        promoted: list[JobRecord] = []
        for job in self.jobs.due_retries():
            if job.spec.project_id != project_id:
                continue
            self.jobs.promote_retry(
                job.job_id,
                reason="retry backoff elapsed",
            )
            self._record_event(
                project,
                action="shot.retry_queued",
                actor=actor,
                message=f"{job.spec.shot_id} retry is ready to run.",
                shot_id=job.spec.shot_id,
                details={
                    "job_id": job.job_id,
                    "attempts": job.attempts,
                    "max_attempts": job.max_attempts,
                },
            )
            promoted.append(job)
            if limit is not None and len(promoted) >= limit:
                break
        if promoted:
            self._persist()
        return promoted

    def drain_queue(
        self,
        project_id: str,
        *,
        limit: int = 50,
        actor: str = "generation-worker",
    ) -> dict[str, Any]:
        project = self._project(project_id)
        self._ensure_active(project)
        promoted_retries = self._promote_due_retries(
            project_id,
            actor=actor,
            limit=limit,
        )
        queued_jobs = [
            job
            for job in self.jobs.all()
            if job.spec.project_id == project_id
            and job.status in {
                JobStatus.VALIDATED,
                JobStatus.QUEUED,
                JobStatus.ADMITTED,
            }
        ][:limit]
        processed = []
        failed = []
        for job in queued_jobs:
            try:
                processed.append(
                    self.process_job(project_id, job.job_id, actor=actor)
                )
            except WorkflowError as exc:
                failed.append(
                    {
                        "job_id": job.job_id,
                        "shot_id": job.spec.shot_id,
                        "status": job.status,
                        "error": str(exc),
                    }
                )
        self._record_event(
            project,
            action="queue.drained",
            actor=actor,
            message=f"{len(processed)} queued job(s) processed.",
            details={
                "processed": len(processed),
                "failed": failed,
                "limit": limit,
            },
        )
        self._persist()
        return {
            "project_id": project_id,
            "processed": len(processed),
            "failed": failed,
            "promoted_retries": [
                job.job_id for job in promoted_retries
            ],
            "remaining": len(self._queued_jobs(project_id)),
            "project": self.project_view(project_id),
        }

    def drain_all_queues(
        self,
        *,
        limit: int = 50,
        actor: str = "generation-worker",
        include_archived: bool = False,
    ) -> dict[str, Any]:
        projects = sorted(
            [
                project
                for project in self.projects.values()
                if include_archived or project.archived_at is None
            ],
            key=lambda project: project.created_at,
        )
        project_reports = []
        processed = 0
        failed: list[dict[str, Any]] = []
        skipped: list[str] = []
        for project in projects:
            remaining_limit = limit - processed
            if remaining_limit <= 0:
                break
            self._promote_due_retries(
                project.brief.project_id,
                actor=actor,
            )
            queued = self._queued_jobs(project.brief.project_id)
            if not queued:
                continue
            try:
                result = self.drain_queue(
                    project.brief.project_id,
                    limit=remaining_limit,
                    actor=actor,
                )
            except WorkflowError as exc:
                skipped.append(project.brief.project_id)
                failed.append(
                    {
                        "project_id": project.brief.project_id,
                        "job_id": None,
                        "shot_id": None,
                        "error": str(exc),
                    }
                )
                continue
            processed += int(result["processed"])
            failed.extend(
                {
                    **item,
                    "project_id": project.brief.project_id,
                }
                for item in result["failed"]
            )
            project_reports.append(
                {
                    "project_id": project.brief.project_id,
                    "processed": result["processed"],
                    "failed": len(result["failed"]),
                    "remaining": result["remaining"],
                }
            )

        return {
            "processed": processed,
            "failed": failed,
            "skipped": skipped,
            "remaining": sum(
                len(self._queued_jobs(project.brief.project_id))
                for project in projects
            ),
            "project_count": len(projects),
            "project_reports": project_reports,
        }

    def submit_pending_shots(self, project_id: str) -> dict[str, Any]:
        project = self._project(project_id)
        self._ensure_active(project)
        if not project.shots:
            raise WorkflowError("generate a plan before submitting shots")

        revisions_requested = [
            shot_id
            for shot_id, runtime in project.shots.items()
            if runtime.current_artifact
            and runtime.review_status == ReviewStatus.CHANGES_REQUESTED
        ]
        if revisions_requested and self.stage_locking_required():
            for shot_id in revisions_requested:
                self.revise_shot(
                    project_id,
                    shot_id,
                    comment="Batch revision requested.",
                )
            self._record_event(
                project,
                action="project.batch_revisions_prepared",
                actor="studio-user",
                message=(
                    f"{len(revisions_requested)} shot revision(s) prepared; "
                    "storyboard and assets must be relocked before generation."
                ),
                details={"prepared_for_stage_lock": revisions_requested},
            )
            self._persist()
            return {
                "project_id": project_id,
                "submitted": 0,
                "skipped": [],
                "prepared_for_stage_lock": revisions_requested,
                "required_stage_locks": ["storyboard", "assets"],
                "project": self.project_view(project_id),
            }

        submitted = []
        skipped = []
        for shot_id, runtime in project.shots.items():
            if runtime.current_artifact and runtime.review_status != ReviewStatus.CHANGES_REQUESTED:
                skipped.append(shot_id)
                continue
            if runtime.current_artifact and runtime.review_status == ReviewStatus.CHANGES_REQUESTED:
                revision = self.revise_shot(
                    project_id,
                    shot_id,
                    comment="Batch revision requested.",
                )
                submitted.append(revision)
                continue
            submitted.append(self.submit_shot(project_id, shot_id))

        self._record_event(
            project,
            action="project.batch_submitted",
            actor="studio-user",
            message=f"{len(submitted)} shot(s) submitted in batch.",
            details={
                "submitted": len(submitted),
                "skipped": skipped,
                "prepared_for_stage_lock": [],
            },
        )
        self._persist()
        return {
            "project_id": project_id,
            "submitted": len(submitted),
            "skipped": skipped,
            "prepared_for_stage_lock": [],
            "project": self.project_view(project_id),
        }

    def review_shot(
        self,
        project_id: str,
        shot_id: str,
        *,
        status: ReviewStatus,
        comment: str = "",
        actor: str = "reviewer",
    ) -> dict[str, Any]:
        project = self._project(project_id)
        self._ensure_active(project)
        runtime = self._shot(project, shot_id)
        if runtime.current_artifact is None:
            raise WorkflowError("shot must be generated before review")
        if status == ReviewStatus.APPROVED and not (runtime.quality and runtime.quality.get("passed")):
            raise WorkflowError("shot quality must pass before approval")
        if status not in {
            ReviewStatus.APPROVED,
            ReviewStatus.CHANGES_REQUESTED,
        }:
            raise WorkflowError("review status must be APPROVED or CHANGES_REQUESTED")
        runtime.review_status = status
        runtime.reviews.append(
            ReviewRecord(
                status=status,
                comment=comment,
                actor=actor,
                occurred_at=datetime.now(timezone.utc),
            )
        )
        self._record_event(
            project,
            action="shot.reviewed",
            actor=actor,
            message=f"{shot_id} reviewed as {status}.",
            shot_id=shot_id,
            details={"status": status, "comment": comment},
        )
        self._persist()
        return self.shot_view(runtime)

    def recheck_shot_quality(self, project_id: str, shot_id: str, *, actor: str = "reviewer") -> dict[str, Any]:
        project = self._project(project_id)
        self._ensure_active(project)
        if project.release is not None:
            raise WorkflowError("released project quality cannot be changed")
        runtime = self._shot(project, shot_id)
        if runtime.current_artifact is None:
            raise WorkflowError("generate the shot before quality review")
        artifact = Artifact.model_validate(runtime.current_artifact)
        revision = runtime.revision
        quality = self._evaluate_artifact_quality(artifact, self._probe_artifact_media(artifact), runtime.spec)
        if project.release is not None or project.archived_at or runtime.revision != revision or not runtime.current_artifact or runtime.current_artifact.get("artifact_id") != artifact.artifact_id:
            raise WorkflowError("shot changed during quality review; retry the current version")
        runtime.quality = quality
        if not quality["passed"]:
            runtime.review_status = ReviewStatus.CHANGES_REQUESTED
            self._invalidate_postproduction(project)
        self._record_event(project, action="shot.quality_rechecked", actor=actor, shot_id=shot_id,
                           message="Shot quality rechecked against current media.", details={"quality": quality, "revision": revision})
        self._persist()
        return self.shot_view(runtime)

    def approve_ready_shots(
        self,
        project_id: str,
        *,
        comment: str = "",
        actor: str = "reviewer",
    ) -> dict[str, Any]:
        project = self._project(project_id)
        self._ensure_active(project)
        if not project.shots:
            raise WorkflowError("generate a plan before approving shots")

        approved = []
        skipped = []
        for shot_id, runtime in project.shots.items():
            if runtime.current_artifact and runtime.review_status == ReviewStatus.PENDING and runtime.quality and runtime.quality.get("passed"):
                self.review_shot(
                    project_id,
                    shot_id,
                    status=ReviewStatus.APPROVED,
                    comment=comment,
                    actor=actor,
                )
                approved.append(shot_id)
            else:
                skipped.append(shot_id)

        self._record_event(
            project,
            action="project.batch_approved",
            actor=actor,
            message=f"{len(approved)} ready shot(s) approved in batch.",
            details={"approved": approved, "skipped": skipped, "comment": comment},
        )
        self._persist()
        return {
            "project_id": project_id,
            "approved": approved,
            "skipped": skipped,
            "project": self.project_view(project_id),
        }


    def revise_shot(
        self,
        project_id: str,
        shot_id: str,
        *,
        comment: str = "",
    ) -> dict[str, Any]:
        project = self._project(project_id)
        self._ensure_active(project)
        runtime = self._shot(project, shot_id)
        if runtime.review_status != ReviewStatus.CHANGES_REQUESTED:
            raise WorkflowError("shot must have requested changes before revision")
        runtime.revision += 1
        runtime.spec = runtime.spec.model_copy(
            update={
                # The graph identity is an approved provider contract. Revision
                # identity belongs in the immutable generation input instead of
                # deriving an unregistered template name.
                "asset_versions": {
                    **runtime.spec.asset_versions,
                    "generation_revision": f"r{runtime.revision}",
                },
            }
        )
        runtime.current_job_id = None
        runtime.current_artifact = None
        runtime.route = None
        runtime.quality = None
        runtime.review_status = ReviewStatus.PENDING
        if comment:
            runtime.reviews.append(
                ReviewRecord(
                    status=ReviewStatus.CHANGES_REQUESTED,
                    comment=comment,
                    actor="revision-agent",
                    occurred_at=datetime.now(timezone.utc),
                )
            )
        self._record_event(
            project,
            action="shot.revision_requested",
            actor="revision-agent",
            message=f"{shot_id} revision {runtime.revision} prepared.",
            shot_id=shot_id,
            details={"revision": runtime.revision, "comment": comment},
        )
        self._invalidate_workflow_from(
            project,
            "assets",
            reason="shot_revision_requested",
            actor="revision-agent",
        )
        self._persist()
        if self.stage_locking_required():
            response = self.shot_view(runtime)
            response["requires_stage_lock"] = True
            response["required_stage_locks"] = ["storyboard", "assets"]
            return response
        return self.submit_shot(project_id, shot_id)

    def export_project(self, project_id: str) -> dict[str, Any]:
        project = self._project(project_id)
        self._ensure_active(project)
        if not project.shots:
            raise WorkflowError("generate a plan before export")
        if project.dialogue_timeline and not self._dialogue_timeline_current(project):
            raise WorkflowError(
                "dialogue timeline is stale; regenerate it before export"
            )
        pending = [
            shot_id
            for shot_id, runtime in project.shots.items()
            if runtime.review_status != ReviewStatus.APPROVED
            or runtime.current_artifact is None
            or not (runtime.quality and runtime.quality.get("passed"))
        ]
        if pending:
            raise WorkflowError(
                f"shots are not approved: {', '.join(sorted(pending))}"
            )
        self._require_production_stage_lock(project, "video")

        output_dir = self.output_root / project_id
        final_mp4 = output_dir / "final_sample.mp4"
        export_inputs = [
            self._artifact_for_export(runtime.current_artifact, runtime, output_dir)
            for runtime in project.shots.values()
        ]
        audio_path = Path(project.audio_track) if project.audio_track else None
        if audio_path and not audio_path.is_file():
            raise WorkflowError("configured audio track is missing")
        concat_target = (
            output_dir / "final_sample.video.mp4"
            if audio_path
            else final_mp4
        )
        concat_videos(export_inputs, concat_target)
        if audio_path:
            mix_audio(concat_target, audio_path, final_mp4)
            concat_target.unlink(missing_ok=True)
        final_quality = probe_video(final_mp4)
        subtitle_path, subtitle_count, subtitle_duration = (
            self._write_project_subtitles(project)
        )
        project.status = ProjectStatus.EXPORTED
        project.final_mp4 = str(final_mp4)
        project.lipsync_artifact = None
        project.subtitle_srt = str(subtitle_path)
        project.delivery_package = None
        project.archive_package = None
        self._record_event(
            project,
            action="project.exported",
            actor="postproduction",
            message="Final MP4 sample exported.",
            details={
                "final_mp4": project.final_mp4,
                "audio_track": project.audio_track,
                "duration_seconds": final_quality.duration_seconds,
                "width": final_quality.width,
                "height": final_quality.height,
                "subtitle_srt": project.subtitle_srt,
                "subtitle_cue_count": subtitle_count,
                "subtitle_duration_seconds": subtitle_duration,
            },
        )
        manifest = self.project_view(project_id)
        manifest["final_quality"] = {
            "passed": final_quality.valid,
            "duration_seconds": final_quality.duration_seconds,
            "width": final_quality.width,
            "height": final_quality.height,
        }
        manifest_path = output_dir / "project-manifest.json"
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=True, indent=2),
            encoding="utf-8",
        )
        self._persist()
        return {
            "project_id": project_id,
            "status": project.status,
            "final_mp4": project.final_mp4,
            "subtitle_srt": project.subtitle_srt,
            "audio_track": project.audio_track,
            "audio_track_metadata": copy.deepcopy(project.audio_track_metadata),
            "manifest": str(manifest_path),
            "final_quality": manifest["final_quality"],
        }

    def build_delivery_package(self, project_id: str) -> dict[str, Any]:
        project = self._project(project_id)
        self._ensure_active(project)
        if project.status != ProjectStatus.EXPORTED or not project.final_mp4:
            raise WorkflowError("export project before packaging delivery assets")
        self._require_production_stage_lock(project, "postproduction")

        output_dir = self.output_root / project_id
        output_root = output_dir.resolve()
        subtitle_path, _, _ = self._ensure_project_subtitles(project)
        manifest_path = output_dir / "project-manifest.json"
        if not manifest_path.is_file():
            self.export_project(project_id)
            project = self._project(project_id)
            manifest_path = output_dir / "project-manifest.json"

        package_path = output_dir / f"{project_id}-delivery.zip"
        previous_package = project.delivery_package
        project.delivery_package = str(package_path)
        project.archive_package = None
        manifest = self.project_view(project_id)
        for document in manifest.get("source_documents", []):
            if not isinstance(document, dict):
                continue
            document_id = str(document.get("document_id") or "")
            document_format = str(document.get("format") or "")
            if document_id and document_format:
                document["uri"] = str(
                    output_dir / "sources" / f"{document_id}.{document_format}"
                )
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=True, indent=2),
            encoding="utf-8",
        )
        sources: dict[Path, str] = {}

        def add_source(path: Path, arcname: str | None = None) -> None:
            resolved = path.resolve()
            if output_root != resolved and output_root not in resolved.parents:
                raise WorkflowError(f"delivery asset is outside project root: {path}")
            if not resolved.is_file():
                raise WorkflowError(f"delivery asset is missing: {path}")
            sources[resolved] = arcname or resolved.relative_to(output_root).as_posix()

        add_source(Path(project.final_mp4), "final_sample.mp4")
        add_source(subtitle_path, "final_subtitles.srt")
        if project.audio_track:
            audio_path = Path(project.audio_track)
            add_source(audio_path, f"final_audio{audio_path.suffix.lower()}")
        add_source(manifest_path, "project-manifest.json")
        for governance_path in self._write_governance_files(project_id):
            add_source(governance_path)
        for credential in project.content_credentials:
            for path_value in (
                credential.get("manifest_path"),
                credential.get("signed_output"),
            ):
                if path_value and Path(str(path_value)).is_file():
                    add_source(Path(str(path_value)))
        for runtime in project.shots.values():
            artifacts = [
                history["artifact"]
                for history in runtime.artifact_history
                if history.get("artifact")
            ]
            artifacts.extend(
                variant["artifact"]
                for variant in runtime.variants
                if variant.get("artifact")
            )
            if runtime.current_artifact:
                artifacts.append(runtime.current_artifact)
            for artifact in artifacts:
                artifact_path = Path(artifact["uri"])
                add_source(artifact_path)
                declared_metadata_uri = artifact.get("metadata_uri")
                metadata_path = Path(
                    str(declared_metadata_uri or artifact_path.with_suffix(".json"))
                )
                if declared_metadata_uri:
                    add_source(metadata_path)
                elif metadata_path.is_file():
                    add_source(metadata_path)
        for reference in project.reference_assets:
            reference_uri = reference.get("uri")
            if reference_uri:
                reference_path = Path(str(reference_uri))
                add_source(reference_path)
        for document in project.source_documents:
            source_path = self._verified_source_document_path(project_id, document)
            add_source(
                source_path,
                f"sources/{document.document_id}.{document.format}",
            )

        files = [
            {
                "path": arcname,
                "size_bytes": path.stat().st_size,
                "sha256": sha256_file(path),
            }
            for path, arcname in sorted(
                sources.items(),
                key=lambda item: item[1],
            )
        ]
        summary = {
            "project_id": project_id,
            "status": project.status,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "provider": self.provider_status,
            "cost": self._cost_report(project),
            "policy": self.policy_report(project_id),
            "compliance": self.project_compliance(project_id),
            "continuity": self.project_continuity(project_id),
            "release": project.release,
            "audio_track": project.audio_track,
            "audio_track_metadata": copy.deepcopy(project.audio_track_metadata),
            "asset_inventory": self.asset_inventory(project_id)["summary"],
            "audit_count": len(project.audit_events),
            "source_document_count": len(project.source_documents),
            "shot_count": len(project.shots),
            "approved_shots": sum(
                1
                for runtime in project.shots.values()
                if runtime.review_status == ReviewStatus.APPROVED
            ),
            "files": files,
        }

        with zipfile.ZipFile(
            package_path,
            mode="w",
            compression=zipfile.ZIP_DEFLATED,
        ) as archive:
            for path, arcname in sorted(sources.items(), key=lambda item: item[1]):
                archive.write(path, arcname)
            archive.writestr(
                "delivery-summary.json",
                json.dumps(summary, ensure_ascii=True, indent=2),
            )

        with zipfile.ZipFile(package_path) as archive:
            verification = self._verify_delivery_archive(
                archive,
                expected_project_id=project_id,
            )
        if not verification["passed"]:
            project.delivery_package = previous_package
            raise WorkflowError(
                "delivery package verification failed: "
                f"{self._verification_error_summary(verification)}"
            )

        verification_report = output_dir / "delivery-verification.json"
        verification["report_path"] = str(verification_report)
        verification_report.write_text(
            json.dumps(verification, ensure_ascii=True, indent=2),
            encoding="utf-8",
        )

        self._record_event(
            project,
            action="project.packaged",
            actor="delivery-service",
            message="Delivery package prepared.",
            details={
                "package_zip": project.delivery_package,
                "verification_passed": True,
                "verification_report": str(verification_report),
                "verified_files": verification["manifest_file_count"],
            },
        )
        self._persist()
        return {
            "project_id": project_id,
            "package_zip": project.delivery_package,
            "size_bytes": package_path.stat().st_size,
            "file_count": len(files) + 1,
            "summary": summary,
            "verification": verification,
        }

    def verify_delivery_package(
        self,
        project_id: str,
        *,
        actor: str = "delivery-qa",
    ) -> dict[str, Any]:
        project = self._project(project_id)
        package_path = project.delivery_package
        if not package_path:
            raise WorkflowError("package delivery before verification")
        path = Path(package_path)
        if not path.is_file():
            raise WorkflowError("delivery package is missing")
        with zipfile.ZipFile(path) as archive:
            verification = self._verify_delivery_archive(archive)
        output_dir = self.output_root / project_id
        output_dir.mkdir(parents=True, exist_ok=True)
        report_path = output_dir / "delivery-verification.json"
        verification["report_path"] = str(report_path)
        report_path.write_text(
            json.dumps(verification, ensure_ascii=True, indent=2),
            encoding="utf-8",
        )
        self._record_event(
            project,
            action="project.package_verified",
            actor=actor,
            message="Delivery package verification completed.",
            details={
                "package_zip": str(path),
                "verification_report": str(report_path),
                "passed": verification["passed"],
                "failed_checks": [
                    check["name"]
                    for check in verification["checks"]
                    if not check["passed"]
                ],
            },
        )
        self._persist()
        return {
            "project_id": project_id,
            "package_zip": str(path),
            "verification_report": str(report_path),
            "verification": verification,
        }

    def prompt_registry(self, project_id: str) -> dict[str, Any]:
        project = self._project(project_id)
        return {
            "project_id": project_id,
            **prompt_registry_view(project.prompt_versions),
        }

    def create_prompt_version(
        self,
        project_id: str,
        *,
        key: str,
        template: str,
        label: str = "",
        activate: bool = False,
        actor: str = "studio-user",
    ) -> dict[str, Any]:
        project = self._project(project_id)
        self._ensure_active(project)
        try:
            prompt = build_prompt_version(
                project.prompt_versions,
                key=key,
                template=template,
                label=label,
                actor=actor,
                activate=activate,
            )
        except PromptValidationError as exc:
            raise WorkflowError(str(exc)) from exc
        if activate:
            for existing in project.prompt_versions:
                if existing.get("key") == prompt["key"]:
                    existing["status"] = "ARCHIVED"
        project.prompt_versions.append(prompt)
        self._record_event(
            project,
            action="llmops.prompt_version_created",
            actor=actor,
            message=f"Prompt {prompt['key']} version {prompt['version']} created.",
            details={
                "prompt_id": prompt["prompt_id"],
                "key": prompt["key"],
                "version": prompt["version"],
                "sha256": prompt["sha256"],
                "active": activate,
            },
        )
        self._persist()
        return {"project_id": project_id, "prompt": copy.deepcopy(prompt), "registry": self.prompt_registry(project_id)}

    def activate_prompt_version(
        self,
        project_id: str,
        prompt_id: str,
        *,
        actor: str = "studio-user",
    ) -> dict[str, Any]:
        project = self._project(project_id)
        self._ensure_active(project)
        try:
            prompt = activate_prompt_version(project.prompt_versions, prompt_id)
        except PromptValidationError as exc:
            raise WorkflowError(str(exc)) from exc
        self._record_event(
            project,
            action="llmops.prompt_activated",
            actor=actor,
            message=f"Prompt {prompt['key']} version {prompt['version']} activated.",
            details={
                "prompt_id": prompt["prompt_id"],
                "key": prompt["key"],
                "version": prompt["version"],
                "sha256": prompt["sha256"],
            },
        )
        self._persist()
        return {"project_id": project_id, "prompt": copy.deepcopy(prompt), "registry": self.prompt_registry(project_id)}

    def _active_prompt_snapshots(self, project: ProjectRuntime) -> list[dict[str, Any]]:
        values = {
            "brief": project.brief.model_dump(mode="json"),
            "project": {"project_id": project.brief.project_id},
        }
        snapshots = []
        for prompt in project.prompt_versions:
            if prompt.get("status") != "ACTIVE":
                continue
            try:
                rendered = render_prompt(prompt, values)
            except PromptValidationError as exc:
                rendered = None
                render_error = str(exc)
            else:
                render_error = None
            snapshots.append(
                {
                    "prompt_id": prompt.get("prompt_id"),
                    "key": prompt.get("key"),
                    "version": prompt.get("version"),
                    "sha256": prompt.get("sha256"),
                    "rendered_sha256": hashlib.sha256(
                        (rendered or "").encode("utf-8")
                    ).hexdigest() if rendered is not None else None,
                    "render_error": render_error,
                }
            )
        return snapshots

    def record_evaluation_annotation(
        self,
        project_id: str,
        *,
        target_type: str,
        target_id: str,
        verdict: str,
        note: str = "",
        rating: float | None = None,
        actor: str = "studio-reviewer",
    ) -> dict[str, Any]:
        project = self._project(project_id)
        self._ensure_active(project)
        clean_type = str(target_type or "").strip().lower()
        clean_id = str(target_id or "").strip()
        clean_verdict = str(verdict or "").strip().upper()
        if clean_type not in {"project", "shot", "asset", "prompt"}:
            raise WorkflowError("annotation target_type must be project, shot, asset or prompt")
        if not clean_id or len(clean_id) > 240:
            raise WorkflowError("annotation target_id is required and must be <= 240 characters")
        if clean_verdict not in {"PASS", "FAIL", "NEEDS_CHANGES", "UNSURE"}:
            raise WorkflowError("annotation verdict must be PASS, FAIL, NEEDS_CHANGES or UNSURE")
        if rating is not None and not 0 <= float(rating) <= 1:
            raise WorkflowError("annotation rating must be between 0 and 1")
        if len(note) > 4_000:
            raise WorkflowError("annotation note must be <= 4000 characters")
        if clean_type == "project" and clean_id != project_id:
            raise WorkflowError("project annotation target_id must match project_id")
        if clean_type == "shot":
            self._shot(project, clean_id)
        elif clean_type == "asset":
            self.asset_download(project_id, clean_id)
        elif clean_type == "prompt" and not any(
            item.get("prompt_id") == clean_id for item in project.prompt_versions
        ):
            raise WorkflowError("prompt annotation target was not found")
        annotation = {
            "annotation_id": f"annotation_{uuid4().hex[:16]}",
            "target_type": clean_type,
            "target_id": clean_id,
            "verdict": clean_verdict,
            "rating": round(float(rating), 4) if rating is not None else None,
            "note": note.strip(),
            "actor": actor.strip() or "studio-reviewer",
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        project.evaluation_annotations.append(annotation)
        self._record_event(
            project,
            action="llmops.annotation_recorded",
            actor=annotation["actor"],
            message=f"Human evaluation recorded for {clean_type} {clean_id}.",
            shot_id=clean_id if clean_type == "shot" else None,
            details={key: annotation[key] for key in ("annotation_id", "target_type", "target_id", "verdict", "rating")},
        )
        self._persist()
        return {"project_id": project_id, "annotation": copy.deepcopy(annotation), "annotation_count": len(project.evaluation_annotations)}

    def register_prompt_experiment(
        self,
        project_id: str,
        *,
        name: str,
        prompt_key: str,
        control_prompt_id: str,
        treatment_prompt_id: str,
        objective: str = "",
        actor: str = "studio-user",
    ) -> dict[str, Any]:
        project = self._project(project_id)
        self._ensure_active(project)
        clean_name = str(name or "").strip()
        clean_key = str(prompt_key or "").strip().lower()
        if not clean_name or len(clean_name) > 240:
            raise WorkflowError("experiment name must contain 1 to 240 characters")
        prompts = {item.get("prompt_id"): item for item in project.prompt_versions}
        control = prompts.get(control_prompt_id)
        treatment = prompts.get(treatment_prompt_id)
        if not control or not treatment:
            raise WorkflowError("experiment prompts were not found")
        if control_prompt_id == treatment_prompt_id:
            raise WorkflowError("experiment control and treatment must differ")
        if control.get("key") != clean_key or treatment.get("key") != clean_key:
            raise WorkflowError("experiment prompts must share the declared prompt key")
        experiment = {
            "experiment_id": f"experiment_{uuid4().hex[:16]}",
            "name": clean_name,
            "objective": str(objective or "").strip()[:2_000],
            "prompt_key": clean_key,
            "control_prompt_id": control_prompt_id,
            "treatment_prompt_id": treatment_prompt_id,
            "status": "REGISTERED",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "created_by": actor.strip() or "studio-user",
            "observations": [],
        }
        project.experiments.append(experiment)
        self._record_event(
            project,
            action="llmops.experiment_registered",
            actor=experiment["created_by"],
            message=f"Prompt experiment {clean_name} registered.",
            details={key: experiment[key] for key in ("experiment_id", "prompt_key", "control_prompt_id", "treatment_prompt_id")},
        )
        self._persist()
        return {"project_id": project_id, "experiment": copy.deepcopy(experiment)}

    def llmops_summary(self, project_id: str) -> dict[str, Any]:
        project = self._project(project_id)
        annotations = copy.deepcopy(project.evaluation_annotations)
        verdicts: dict[str, int] = {}
        for annotation in annotations:
            verdict = str(annotation.get("verdict") or "UNSURE")
            verdicts[verdict] = verdicts.get(verdict, 0) + 1
        return {
            "schema_version": "mediaforge-llmops-v1",
            "project_id": project_id,
            "prompt_registry": self.prompt_registry(project_id),
            "active_prompt_snapshots": self._active_prompt_snapshots(project),
            "annotation_count": len(annotations),
            "annotations": annotations,
            "annotation_verdicts": verdicts,
            "experiment_count": len(project.experiments),
            "experiments": copy.deepcopy(project.experiments),
            "latest_evaluation": copy.deepcopy(project.evaluations[-1]) if project.evaluations else None,
            "trace_id": project.trace_id,
        }

    def asset_derivatives(self, project_id: str, asset_id: str, *, actor: str = "studio-user") -> dict[str, Any]:
        project = self._project(project_id)
        self._ensure_active(project)
        asset = self.asset_download(project_id, asset_id)
        media_kind = str(asset.get("kind") or "")
        if media_kind in {"final_mp4", "lipsync_output"}:
            media_kind = "video"
        elif media_kind == "audio_track":
            media_kind = "audio"
        elif media_kind in {"character_reference", "style_reference", "location_reference"}:
            media_kind = "image"
        if media_kind not in {"image", "video", "audio"}:
            raise WorkflowError("asset does not support media derivatives")
        source_hash = str(asset["sha256"])
        existing = project.media_derivatives.get(asset_id, [])
        if existing and all(item.get("source_sha256") == source_hash for item in existing):
            return {"project_id": project_id, "asset_id": asset_id, "derivatives": copy.deepcopy(existing), "cached": True}
        directory = self.output_root / project_id / "derivatives" / hashlib.sha256(asset_id.encode("utf-8")).hexdigest()[:20]
        try:
            records = create_media_derivatives(
                Path(asset["path"]),
                media_kind=media_kind,
                output_directory=directory,
                duration_seconds=(
                    probe_video(Path(asset["path"])).duration_seconds
                    if media_kind == "video"
                    else None
                ),
            )
        except (OSError, ValueError, RuntimeError) as exc:
            raise WorkflowError(f"media derivative generation failed: {exc}") from exc
        project.media_derivatives[asset_id] = records
        self._record_event(
            project,
            action="asset.derivatives_generated",
            actor=actor,
            message=f"Generated {len(records)} media derivatives for {asset_id}.",
            details={"asset_id": asset_id, "source_sha256": source_hash, "derivative_count": len(records)},
        )
        self._persist()
        return {"project_id": project_id, "asset_id": asset_id, "derivatives": copy.deepcopy(records), "cached": False}

    def asset_derivative_download(self, project_id: str, asset_id: str, derivative_id: str) -> dict[str, Any]:
        project = self._project(project_id)
        derivatives = project.media_derivatives.get(asset_id, [])
        record = next((item for item in derivatives if item.get("derivative_id") == derivative_id), None)
        if not record or not record.get("available") or not record.get("uri"):
            raise WorkflowError("media derivative was not found")
        path = Path(str(record["uri"])).resolve()
        root = self.output_root.resolve()
        if root != path and root not in path.parents:
            raise WorkflowError("media derivative is outside the configured artifact root")
        if not path.is_file():
            raise WorkflowError("media derivative object is missing")
        return {
            "project_id": project_id,
            "asset_id": asset_id,
            "derivative_id": derivative_id,
            "path": path,
            "filename": path.name,
            "media_type": record.get("mime_type") or mimetypes.guess_type(path.name)[0] or "application/octet-stream",
            "sha256": record.get("sha256") or sha256_file(path),
            "size_bytes": path.stat().st_size,
        }

    def content_credentials_status(self) -> dict[str, Any]:
        return self.content_credentials.status_view()

    def _credential_asset(self, project_id: str, asset_id: str) -> dict[str, Any]:
        inventory = self.asset_inventory(project_id)
        for asset in [
            *inventory["reference_assets"],
            *inventory["generated_assets"],
        ]:
            if asset.get("asset_id") == asset_id and asset.get("uri"):
                return asset
        for output in inventory["outputs"]:
            if output.get("kind") == asset_id and output.get("uri"):
                return {**output, "asset_id": asset_id}
        raise WorkflowError("content credential asset was not found or has no local media file")

    def project_content_credentials(self, project_id: str) -> dict[str, Any]:
        project = self._project(project_id)
        credentials = copy.deepcopy(project.content_credentials)
        status_counts: dict[str, int] = {}
        verification_counts: dict[str, int] = {}
        for credential in credentials:
            status = str((credential.get("c2pa") or {}).get("status") or "UNKNOWN")
            status_counts[status] = status_counts.get(status, 0) + 1
            verification_status = str(
                (credential.get("verification") or {}).get("status")
                or "NOT_VERIFIED"
            )
            verification_counts[verification_status] = (
                verification_counts.get(verification_status, 0) + 1
            )

        # A release credential must prove the exact bytes of the current final
        # delivery, not merely one of the project's source or generated assets.
        # Re-exporting the project changes this hash and makes prior final-media
        # credentials ineligible until the new output is signed and verified.
        final_output = next(
            (
                output
                for output in self.asset_inventory(project_id)["outputs"]
                if output.get("kind") == "final_mp4"
            ),
            None,
        )
        final_sha256 = str((final_output or {}).get("sha256") or "")
        final_available = bool(
            final_output
            and final_output.get("exists")
            and final_sha256
        )
        final_credentials = [
            credential
            for credential in credentials
            if str((credential.get("claim") or {}).get("asset_id") or "")
            == "final_mp4"
        ]
        signed_verified_final_credentials = [
            credential
            for credential in final_credentials
            if (credential.get("c2pa") or {}).get("status") == "SIGNED_VERIFIED"
            and (credential.get("verification") or {}).get("status")
            == "SIGNED_VERIFIED"
        ]
        current_signed_verified_final_credentials = [
            credential
            for credential in signed_verified_final_credentials
            if final_available
            and str((credential.get("asset") or {}).get("sha256") or "")
            == final_sha256
        ]
        return {
            "schema_version": "mediaforge-project-content-credentials-v2",
            "project_id": project_id,
            "adapter": self.content_credentials_status(),
            "credentials": credentials,
            "summary": {
                "count": len(credentials),
                "by_status": status_counts,
                "by_verification_status": verification_counts,
                "signed_count": sum(
                    1
                    for credential in credentials
                    if (credential.get("c2pa") or {}).get("status") == "SIGNED_UNVERIFIED"
                ),
                "final_media": {
                    "asset_id": "final_mp4",
                    "available": final_available,
                    "credential_count": len(final_credentials),
                    "signed_verified_count": len(signed_verified_final_credentials),
                    "current_signed_verified_count": len(
                        current_signed_verified_final_credentials
                    ),
                    "ready": bool(current_signed_verified_final_credentials),
                },
            },
        }

    def create_content_credential(
        self,
        project_id: str,
        *,
        asset_id: str,
        actor: str = "studio-governance",
    ) -> dict[str, Any]:
        project = self._project(project_id)
        self._ensure_active(project)
        asset = self._credential_asset(project_id, asset_id.strip())
        asset_path = Path(str(asset["uri"]))
        claim = {
            "generator": "MediaForge",
            "actor": actor,
            "asset_id": asset_id.strip(),
            "asset_kind": asset.get("kind"),
            "provider": asset.get("provider"),
            "project_title": project.brief.title,
            "project_status": project.status,
            "source_references": [
                {
                    "asset_id": reference.get("asset_id"),
                    "sha256": reference.get("sha256"),
                    "license": reference.get("license"),
                }
                for reference in project.reference_assets
            ],
            "audit": {
                "event_count": len(project.audit_events),
                "integrity": self.audit_integrity_report(project_id).get("valid"),
                "anchor_count": len(project.audit_anchors),
            },
        }
        try:
            credential = self.content_credentials.create(
                project_id=project_id,
                asset_path=asset_path,
                output_dir=self.output_root / project_id / "content-credentials",
                claim=claim,
            )
        except ContentCredentialError as exc:
            raise WorkflowError(str(exc)) from exc
        credential["asset_path"] = str(asset_path.resolve())
        credential["verification"] = {
            "status": "NOT_VERIFIED",
            "passed": False,
            "checked_at": None,
        }
        project.content_credentials = [
            item
            for item in project.content_credentials
            if item.get("credential_id") != credential.get("credential_id")
        ]
        project.content_credentials.append(copy.deepcopy(credential))
        self._record_event(
            project,
            action="content_credentials.created",
            actor=actor,
            message="Content credential claim created.",
            details={
                "credential_id": credential["credential_id"],
                "asset_id": asset_id.strip(),
                "c2pa_status": credential["c2pa"]["status"],
                "manifest_path": credential["manifest_path"],
            },
        )
        self._persist()
        return {"project_id": project_id, "credential": copy.deepcopy(credential)}

    def verify_content_credential(
        self,
        project_id: str,
        credential_id: str,
        *,
        actor: str = "studio-governance",
    ) -> dict[str, Any]:
        project = self._project(project_id)
        credential = next(
            (
                item
                for item in project.content_credentials
                if item.get("credential_id") == credential_id.strip()
            ),
            None,
        )
        if credential is None:
            raise WorkflowError("content credential was not found")
        if not credential.get("asset_path"):
            # Claims created before the verifier feature did not persist their
            # source path. Recover it from the durable asset identifier when possible.
            asset_id = str((credential.get("claim") or {}).get("asset_id") or "")
            if asset_id:
                try:
                    asset = self._credential_asset(project_id, asset_id)
                    credential["asset_path"] = str(Path(str(asset["uri"])).resolve())
                except WorkflowError:
                    pass
        try:
            verification = self.content_credentials.verify(credential)
        except ContentCredentialError as exc:
            raise WorkflowError(str(exc)) from exc
        credential["verification"] = verification
        if str(verification.get("status") or "").startswith("SIGNED"):
            c2pa = credential.get("c2pa")
            if not isinstance(c2pa, dict):
                c2pa = {}
                credential["c2pa"] = c2pa
            c2pa["status"] = verification["status"]
            c2pa["verification"] = (
                "independent-verifier-passed"
                if verification.get("status") == "SIGNED_VERIFIED"
                else "independent-verifier-did-not-pass"
            )
        self._record_event(
            project,
            action="content_credentials.verified",
            actor=actor,
            message="Content credential verification completed.",
            details={
                "credential_id": credential["credential_id"],
                "verification_status": verification["status"],
                "passed": verification["passed"],
            },
        )
        self._persist()
        return {
            "project_id": project_id,
            "credential": copy.deepcopy(credential),
            "verification": copy.deepcopy(verification),
        }

    def edit_timeline(self, project_id: str) -> dict[str, Any]:
        project = self._project(project_id)
        if project.edit_timeline:
            return copy.deepcopy(project.edit_timeline)
        cursor = 0.0
        clips = []
        for runtime in project.shots.values():
            duration = float(runtime.shot.duration_seconds)
            clips.append(
                {
                    "clip_id": f"clip_{runtime.shot.shot_id}",
                    "shot_id": runtime.shot.shot_id,
                    "source_asset_id": (runtime.current_artifact or {}).get("artifact_id"),
                    "start_seconds": round(cursor, 3),
                    "duration_seconds": duration,
                    "enabled": True,
                }
            )
            cursor += duration
        return {
            "schema_version": "mediaforge-edit-timeline-v1",
            "timeline_id": f"timeline_{project_id}",
            "project_id": project_id,
            "revision": 0,
            "duration_seconds": round(cursor, 3),
            "video_clips": clips,
            "audio_track": project.audio_track,
            "subtitle_srt": project.subtitle_srt,
            "generated_at": project.created_at.isoformat(),
            "current": True,
        }

    def update_edit_timeline(
        self,
        project_id: str,
        *,
        clips: list[dict[str, Any]],
        actor: str = "studio-editor",
    ) -> dict[str, Any]:
        project = self._project(project_id)
        self._ensure_active(project)
        if project.release is not None:
            raise WorkflowError("released project timeline cannot be changed")
        if not clips or len(clips) > 500:
            raise WorkflowError("timeline requires 1 to 500 clips")
        seen: set[str] = set()
        normalized: list[dict[str, Any]] = []
        cursor = 0.0
        for index, item in enumerate(clips, start=1):
            shot_id = str(item.get("shot_id") or "").strip()
            runtime = self._shot(project, shot_id)
            if shot_id in seen:
                raise WorkflowError("timeline clip shot_id values must be unique")
            seen.add(shot_id)
            enabled = bool(item.get("enabled", True))
            duration = float(item.get("duration_seconds", runtime.shot.duration_seconds))
            if duration <= 0 or duration > 60:
                raise WorkflowError("timeline clip duration_seconds must be between 0 and 60")
            start = float(item.get("start_seconds", cursor))
            if start < cursor - 0.001:
                raise WorkflowError("timeline clips must be ordered without overlap")
            normalized.append(
                {
                    "clip_id": str(item.get("clip_id") or f"clip_{shot_id}"),
                    "shot_id": shot_id,
                    "source_asset_id": (runtime.current_artifact or {}).get("artifact_id"),
                    "start_seconds": round(start, 3),
                    "duration_seconds": round(duration, 3),
                    "enabled": enabled,
                }
            )
            cursor = start + (duration if enabled else 0)
        prior = self.edit_timeline(project_id)
        project.edit_timeline = {
            **prior,
            "revision": int(prior.get("revision", 0)) + 1,
            "duration_seconds": round(cursor, 3),
            "video_clips": normalized,
            "audio_track": project.audio_track,
            "subtitle_srt": project.subtitle_srt,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "current": True,
        }
        self._invalidate_postproduction(project)
        self._record_event(
            project,
            action="timeline.updated",
            actor=actor,
            message="Edit timeline updated.",
            details={"timeline_id": project.edit_timeline["timeline_id"], "revision": project.edit_timeline["revision"], "clip_count": len(normalized)},
        )
        self._persist()
        return copy.deepcopy(project.edit_timeline)

    def export_edit_timeline(self, project_id: str, *, format: str, actor: str = "studio-editor") -> dict[str, Any]:
        project = self._project(project_id)
        timeline = self.edit_timeline(project_id)
        clean_format = str(format or "").strip().lower()
        output_dir = self.output_root / project_id / "timeline"
        output_dir.mkdir(parents=True, exist_ok=True)
        if clean_format == "otio_json":
            path = output_dir / "timeline.otio.json"
            payload = {
                "schema_version": "mediaforge-otio-interchange-v1",
                "format": "otio_json",
                "timeline": timeline,
                "note": "Portable edit decision list. Convert with OpenTimelineIO adapters for NLE-specific formats.",
            }
            path.write_text(json.dumps(payload, ensure_ascii=True, indent=2), encoding="utf-8")
        elif clean_format == "edl_csv":
            path = output_dir / "timeline.edl.csv"
            with path.open("w", encoding="utf-8", newline="") as stream:
                writer = csv.DictWriter(stream, fieldnames=["clip_id", "shot_id", "source_asset_id", "start_seconds", "duration_seconds", "enabled"])
                writer.writeheader()
                writer.writerows(timeline["video_clips"])
        else:
            raise WorkflowError("timeline format must be otio_json or edl_csv")
        self._record_event(
            project,
            action="timeline.exported",
            actor=actor,
            message=f"Edit timeline exported as {clean_format}.",
            details={"format": clean_format, "path": str(path), "revision": timeline["revision"]},
        )
        self._persist()
        return {"project_id": project_id, "format": clean_format, "timeline": timeline, "path": str(path)}

    def asset_inventory(self, project_id: str) -> dict[str, Any]:
        project = self._project(project_id)
        reference_assets = []
        for character in project.brief.characters:
            selected = next(
                (
                    asset
                    for asset in reversed(project.reference_assets)
                    if asset.get("kind") == "character_reference"
                    and asset.get("character") == character
                ),
                None,
            )
            reference = copy.deepcopy(selected) if selected else {
                "asset_id": f"{project_id}:character:{character}:v1",
                "kind": "character_reference",
                "name": character,
                "version": "v1",
                "source": "creative_brief",
                "license": "user_supplied_or_project_owned",
            }
            reference["used_by_shots"] = [
                runtime.shot.shot_id
                for runtime in project.shots.values()
                if character in runtime.shot.characters
                and (
                    selected is None
                    or reference["asset_id"] == selected.get("asset_id")
                )
            ]
            reference["derivatives"] = copy.deepcopy(
                project.media_derivatives.get(reference["asset_id"], [])
            )
            reference_assets.append(reference)

        active_reference_ids = {
            asset["asset_id"]
            for asset in reference_assets
            if asset.get("source") != "creative_brief"
        }
        for asset in project.reference_assets:
            if asset.get("asset_id") in active_reference_ids:
                continue
            historical = copy.deepcopy(asset)
            historical["used_by_shots"] = []
            historical["derivatives"] = copy.deepcopy(
                project.media_derivatives.get(historical.get("asset_id", ""), [])
            )
            reference_assets.append(historical)

        generated_assets = []
        seen_artifacts: set[str] = set()
        for runtime in project.shots.values():
            asset_entries = [
                *runtime.artifact_history,
                *[
                    {
                        "revision": variant.get("revision", runtime.revision),
                        "job_id": variant.get("job_id"),
                        "variant_id": variant.get("variant_id"),
                        "artifact": variant.get("artifact"),
                        "route": variant.get("route"),
                        "quality": variant.get("quality"),
                        "score": variant.get("score"),
                        "candidate": True,
                        "promoted": bool(variant.get("promoted")),
                        "created_at": variant.get("created_at"),
                    }
                    for variant in runtime.variants
                ],
            ]
            for entry in asset_entries:
                artifact = entry.get("artifact")
                if not artifact or artifact["artifact_id"] in seen_artifacts:
                    continue
                seen_artifacts.add(artifact["artifact_id"])
                route = entry.get("route") or {}
                generated_assets.append(
                    {
                        "asset_id": artifact["artifact_id"],
                        "kind": artifact["kind"],
                        "uri": artifact["uri"],
                        "metadata_uri": artifact.get("metadata_uri"),
                        "mime_type": artifact["mime_type"],
                        "sha256": artifact["sha256"],
                        "size_bytes": artifact["size_bytes"],
                        "duration_seconds": artifact.get("duration_seconds"),
                        "shot_id": runtime.shot.shot_id,
                        "revision": entry.get("revision", runtime.revision),
                        "job_id": entry.get("job_id"),
                        "variant_id": entry.get("variant_id"),
                        "provider": route.get("provider"),
                        "current": (
                            runtime.current_artifact is not None
                            and runtime.current_artifact.get("artifact_id")
                            == artifact["artifact_id"]
                        ),
                        "candidate": bool(entry.get("candidate")),
                        "promoted": bool(entry.get("promoted")),
                        "score": entry.get("score"),
                        "created_at": artifact.get("created_at") or entry.get("created_at"),
                        "derivatives": copy.deepcopy(
                            project.media_derivatives.get(artifact["artifact_id"], [])
                        ),
                    }
                )

        outputs = []
        for kind, uri in {
            "final_mp4": project.final_mp4,
            "lipsync_output": (project.lipsync_artifact or {}).get("uri") if project.lipsync_artifact else None,
            "subtitle_srt": project.subtitle_srt,
            "audio_track": project.audio_track,
            "delivery_package": project.delivery_package,
        }.items():
            if not uri:
                continue
            path = Path(uri)
            output = {
                "kind": kind,
                "uri": uri,
                "exists": path.is_file(),
                "size_bytes": path.stat().st_size if path.is_file() else None,
                "sha256": sha256_file(path) if path.is_file() else None,
                "derivatives": copy.deepcopy(
                    project.media_derivatives.get(kind, [])
                ),
            }
            if kind == "audio_track" and project.audio_track_metadata:
                output.update(copy.deepcopy(project.audio_track_metadata))
                output["kind"] = kind
                output["uri"] = uri
                output["exists"] = path.is_file()
            outputs.append(output)

        generated_by_kind = {"image": 0, "video": 0}
        current_by_kind = {"image": 0, "video": 0}
        for asset in generated_assets:
            kind = asset["kind"]
            if kind in generated_by_kind:
                generated_by_kind[kind] += 1
            if asset["current"] and kind in current_by_kind:
                current_by_kind[kind] += 1

        derivative_count = sum(
            len(items) for items in project.media_derivatives.values()
        )

        return {
            "project_id": project_id,
            "summary": {
                "reference_assets": len(reference_assets),
                "generated_assets": len(generated_assets),
                "generated_by_kind": generated_by_kind,
                "outputs": len(outputs),
                "derivatives": derivative_count,
                "current_video_assets": sum(
                    1
                    for asset in generated_assets
                    if asset["kind"] == "video" and asset["current"]
                ),
                "current_media_assets": (
                    current_by_kind["image"] + current_by_kind["video"]
                ),
                "current_by_kind": current_by_kind,
            },
            "reference_assets": reference_assets,
            "generated_assets": generated_assets,
            "outputs": outputs,
        }

    def asset_download(self, project_id: str, asset_id: str) -> dict[str, Any]:
        """Resolve a catalogued asset to a safe, downloadable local object."""
        project = self._project(project_id)
        clean_id = str(asset_id or "").strip()
        if not clean_id or len(clean_id) > 240:
            raise WorkflowError("asset_id is required and must be <= 240 characters")
        inventory = self.asset_inventory(project_id)
        candidates: list[dict[str, Any]] = [
            asset
            for asset in inventory["generated_assets"]
            if asset.get("asset_id") == clean_id
        ]
        candidates.extend(
            asset
            for asset in inventory["reference_assets"]
            if asset.get("asset_id") == clean_id
        )
        candidates.extend(
            {
                "asset_id": output["kind"],
                "kind": output["kind"],
                "uri": output.get("uri"),
                "mime_type": mimetypes.guess_type(str(output.get("uri") or ""))[0],
                "sha256": output.get("sha256"),
                "size_bytes": output.get("size_bytes"),
            }
            for output in inventory["outputs"]
            if output.get("kind") == clean_id
        )
        if not candidates:
            raise WorkflowError(f"asset not found: {clean_id}")
        record = candidates[0]
        uri = str(record.get("uri") or "").strip()
        if not uri:
            raise WorkflowError(f"asset has no downloadable object: {clean_id}")
        try:
            path = Path(uri).resolve()
            root = self.output_root.resolve()
        except OSError as exc:
            raise WorkflowError("asset path is invalid") from exc
        if root != path and root not in path.parents:
            raise WorkflowError("asset is outside the configured artifact root")
        if not path.is_file():
            raise WorkflowError("asset object is missing")
        return {
            "asset_id": clean_id,
            "kind": record.get("kind"),
            "path": path,
            "filename": path.name,
            "media_type": record.get("mime_type")
            or mimetypes.guess_type(path.name)[0]
            or "application/octet-stream",
            "sha256": record.get("sha256") or sha256_file(path),
            "size_bytes": path.stat().st_size,
            "project_id": project.brief.project_id,
        }

    @staticmethod
    def _verification_error_summary(verification: dict[str, Any]) -> str:
        failures: list[str] = []
        for check in verification.get("checks", []):
            if check.get("passed", True):
                continue
            name = check.get("name", "check")
            if name == "file_set":
                missing = verification.get("missing_files") or []
                extra = verification.get("extra_files") or []
                if missing:
                    failures.append(f"missing: {', '.join(missing[:3])}")
                if extra:
                    failures.append(f"unexpected: {', '.join(extra[:3])}")
                continue
            observed = check.get("observed")
            expected = check.get("expected")
            failures.append(f"{name}: {observed} != {expected}")
        if not failures:
            errors = verification.get("errors") or []
            if errors:
                failures.extend(str(item) for item in errors[:3])
        return "; ".join(failures) if failures else "verification failed"

    def _verify_delivery_archive(
        self,
        archive: zipfile.ZipFile,
        *,
        expected_project_id: str | None = None,
    ) -> dict[str, Any]:
        checked_at = datetime.now(timezone.utc).isoformat()
        names = [name for name in archive.namelist() if not name.endswith("/")]
        name_set = set(names)
        checks: list[dict[str, Any]] = []
        errors: list[str] = []

        def add_check(
            name: str,
            passed: bool,
            observed: Any,
            expected: Any,
        ) -> None:
            checks.append(
                {
                    "name": name,
                    "passed": bool(passed),
                    "observed": observed,
                    "expected": expected,
                }
            )

        if len(name_set) != len(names):
            add_check("duplicate_entries", False, len(names), len(name_set))
            errors.append("duplicate archive entries detected")
        unsafe_names = sorted(
            name for name in name_set if not self._safe_archive_name(name)
        )
        add_check("safe_paths", not unsafe_names, unsafe_names, [])
        if unsafe_names:
            errors.append("unsafe archive paths detected")

        if "delivery-summary.json" not in name_set:
            add_check("delivery-summary.json", False, None, "present")
            return {
                "schema_version": "mediaforge-delivery-verification-v1",
                "checked_at": checked_at,
                "passed": False,
                "package_project_id": None,
                "expected_project_id": expected_project_id,
                "archive_file_count": len(names),
                "manifest_file_count": 0,
                "missing_files": [],
                "extra_files": sorted(name_set),
                "checks": checks,
                "errors": errors or ["missing delivery-summary.json"],
            }

        try:
            summary = json.loads(
                archive.read("delivery-summary.json").decode("utf-8")
            )
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            add_check("delivery-summary.json", False, str(exc), "valid JSON")
            errors.append("delivery-summary.json is invalid")
            return {
                "schema_version": "mediaforge-delivery-verification-v1",
                "checked_at": checked_at,
                "passed": False,
                "package_project_id": None,
                "expected_project_id": expected_project_id,
                "archive_file_count": len(names),
                "manifest_file_count": 0,
                "missing_files": [],
                "extra_files": sorted(name_set),
                "checks": checks,
                "errors": errors,
            }

        package_project_id = summary.get("project_id")
        if expected_project_id is None:
            add_check(
                "project_id",
                bool(package_project_id),
                package_project_id,
                "present",
            )
        else:
            add_check(
                "project_id",
                package_project_id == expected_project_id,
                package_project_id,
                expected_project_id,
            )

        files = summary.get("files")
        if not isinstance(files, list):
            add_check("files", False, type(files).__name__, "list")
            errors.append("delivery-summary.json is missing files list")
            return {
                "schema_version": "mediaforge-delivery-verification-v1",
                "checked_at": checked_at,
                "passed": False,
                "package_project_id": package_project_id,
                "expected_project_id": expected_project_id,
                "archive_file_count": len(names),
                "manifest_file_count": 0,
                "missing_files": [],
                "extra_files": sorted(name_set - {"delivery-summary.json"}),
                "checks": checks,
                "errors": errors,
            }

        expected_paths: list[str] = []
        file_rows: list[dict[str, Any]] = []
        for item in files:
            if not isinstance(item, dict):
                errors.append("delivery-summary.json contains a non-object file entry")
                continue
            path = item.get("path")
            if not isinstance(path, str):
                errors.append("delivery-summary.json contains a file without a path")
                continue
            expected_paths.append(path)
            file_rows.append(item)

        expected_set = set(expected_paths)
        actual_set = name_set - {"delivery-summary.json"}
        missing = sorted(expected_set - actual_set)
        extra = sorted(actual_set - expected_set)
        add_check(
            "file_set",
            not missing and not extra,
            {"missing": missing, "extra": extra},
            {"files": sorted(expected_set)},
        )

        for item in file_rows:
            path = item["path"]
            if path not in actual_set:
                add_check(
                    f"file:{path}",
                    False,
                    "missing",
                    {
                        "size_bytes": item.get("size_bytes"),
                        "sha256": item.get("sha256"),
                    },
                )
                continue
            data = archive.read(path)
            observed = {
                "size_bytes": len(data),
                "sha256": hashlib.sha256(data).hexdigest(),
            }
            expected = {
                "size_bytes": item.get("size_bytes"),
                "sha256": item.get("sha256"),
            }
            add_check(
                f"file:{path}",
                observed == expected,
                observed,
                expected,
            )

        passed = not errors and all(check["passed"] for check in checks)
        return {
            "schema_version": "mediaforge-delivery-verification-v1",
            "checked_at": checked_at,
            "passed": passed,
            "package_project_id": package_project_id,
            "expected_project_id": expected_project_id,
            "archive_file_count": len(names),
            "manifest_file_count": len(file_rows),
            "missing_files": missing,
            "extra_files": extra,
            "checks": checks,
            "errors": errors,
        }

    def _verify_manifest_archive(
        self,
        archive: zipfile.ZipFile,
        *,
        summary_name: str,
        schema_version: str,
        expected_project_id: str | None = None,
    ) -> dict[str, Any]:
        checked_at = datetime.now(timezone.utc).isoformat()
        names = [name for name in archive.namelist() if not name.endswith("/")]
        name_set = set(names)
        checks: list[dict[str, Any]] = []
        errors: list[str] = []

        def add_check(
            name: str,
            passed: bool,
            observed: Any,
            expected: Any,
        ) -> None:
            checks.append(
                {
                    "name": name,
                    "passed": bool(passed),
                    "observed": observed,
                    "expected": expected,
                }
            )

        if len(name_set) != len(names):
            add_check("duplicate_entries", False, len(names), len(name_set))
            errors.append("duplicate archive entries detected")
        unsafe_names = sorted(
            name for name in name_set if not self._safe_archive_name(name)
        )
        add_check("safe_paths", not unsafe_names, unsafe_names, [])
        if unsafe_names:
            errors.append("unsafe archive paths detected")

        if summary_name not in name_set:
            add_check(summary_name, False, None, "present")
            return {
                "schema_version": schema_version,
                "checked_at": checked_at,
                "passed": False,
                "package_project_id": None,
                "expected_project_id": expected_project_id,
                "archive_file_count": len(names),
                "manifest_file_count": 0,
                "missing_files": [],
                "extra_files": sorted(name_set),
                "checks": checks,
                "errors": errors or [f"missing {summary_name}"],
            }

        try:
            summary = json.loads(archive.read(summary_name).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            add_check(summary_name, False, str(exc), "valid JSON")
            errors.append(f"{summary_name} is invalid")
            return {
                "schema_version": schema_version,
                "checked_at": checked_at,
                "passed": False,
                "package_project_id": None,
                "expected_project_id": expected_project_id,
                "archive_file_count": len(names),
                "manifest_file_count": 0,
                "missing_files": [],
                "extra_files": sorted(name_set),
                "checks": checks,
                "errors": errors,
            }

        package_project_id = summary.get("project_id")
        if expected_project_id is None:
            add_check(
                "project_id",
                bool(package_project_id),
                package_project_id,
                "present",
            )
        else:
            add_check(
                "project_id",
                package_project_id == expected_project_id,
                package_project_id,
                expected_project_id,
            )

        files = summary.get("files")
        if not isinstance(files, list):
            add_check("files", False, type(files).__name__, "list")
            errors.append(f"{summary_name} is missing files list")
            return {
                "schema_version": schema_version,
                "checked_at": checked_at,
                "passed": False,
                "package_project_id": package_project_id,
                "expected_project_id": expected_project_id,
                "archive_file_count": len(names),
                "manifest_file_count": 0,
                "missing_files": [],
                "extra_files": sorted(name_set - {summary_name}),
                "checks": checks,
                "errors": errors,
            }

        expected_paths: list[str] = []
        file_rows: list[dict[str, Any]] = []
        for item in files:
            if not isinstance(item, dict):
                errors.append(f"{summary_name} contains a non-object file entry")
                continue
            path = item.get("path")
            if not isinstance(path, str):
                errors.append(f"{summary_name} contains a file without a path")
                continue
            expected_paths.append(path)
            file_rows.append(item)

        expected_set = set(expected_paths)
        actual_set = name_set - {summary_name}
        missing = sorted(expected_set - actual_set)
        extra = sorted(actual_set - expected_set)
        add_check(
            "file_set",
            not missing and not extra,
            {"missing": missing, "extra": extra},
            {"files": sorted(expected_set)},
        )

        for item in file_rows:
            path = item["path"]
            if path not in actual_set:
                add_check(
                    f"file:{path}",
                    False,
                    "missing",
                    {
                        "size_bytes": item.get("size_bytes"),
                        "sha256": item.get("sha256"),
                    },
                )
                continue
            data = archive.read(path)
            observed = {
                "size_bytes": len(data),
                "sha256": hashlib.sha256(data).hexdigest(),
            }
            expected = {
                "size_bytes": item.get("size_bytes"),
                "sha256": item.get("sha256"),
            }
            add_check(f"file:{path}", observed == expected, observed, expected)

        passed = not errors and all(check["passed"] for check in checks)
        return {
            "schema_version": schema_version,
            "checked_at": checked_at,
            "passed": passed,
            "package_project_id": package_project_id,
            "expected_project_id": expected_project_id,
            "archive_file_count": len(names),
            "manifest_file_count": len(file_rows),
            "missing_files": missing,
            "extra_files": extra,
            "checks": checks,
            "errors": errors,
        }

    @staticmethod
    def _safe_archive_name(name: str) -> bool:
        normalized = name.replace("\\", "/")
        if normalized != name or not normalized or normalized.startswith("/"):
            return False
        parts = normalized.split("/")
        if any(part in {"", ".", ".."} for part in parts):
            return False
        return ":" not in parts[0]

    def _delivery_verification_state(
        self,
        project: ProjectRuntime,
    ) -> tuple[bool, Path]:
        verification_report = self._delivery_verification_report(
            project.brief.project_id
        )
        package_path = (
            Path(project.delivery_package) if project.delivery_package else None
        )
        if (
            not package_path
            or not package_path.is_file()
            or not verification_report.is_file()
        ):
            return False, verification_report
        try:
            verification = json.loads(
                verification_report.read_text(encoding="utf-8")
            )
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            return False, verification_report
        if not verification.get("passed"):
            return False, verification_report
        if (
            package_path.stat().st_mtime_ns
            > verification_report.stat().st_mtime_ns
        ):
            return False, verification_report
        return True, verification_report

    def _archive_verification_state(
        self,
        project: ProjectRuntime,
    ) -> tuple[bool, Path]:
        verification_report = self._archive_verification_report(
            project.brief.project_id
        )
        package_path = (
            Path(project.archive_package) if project.archive_package else None
        )
        if (
            not package_path
            or not package_path.is_file()
            or not verification_report.is_file()
        ):
            return False, verification_report
        try:
            verification = json.loads(
                verification_report.read_text(encoding="utf-8")
            )
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            return False, verification_report
        if not verification.get("passed"):
            return False, verification_report
        if (
            package_path.stat().st_mtime_ns
            > verification_report.stat().st_mtime_ns
        ):
            return False, verification_report
        return True, verification_report

    def _delivery_verification_report(self, project_id: str) -> Path:
        return self.output_root / project_id / "delivery-verification.json"

    @staticmethod
    def _delivery_status(delivery: dict[str, Any] | None) -> str | None:
        if delivery is None:
            return None
        return str(delivery.get("status") or "DELIVERED").upper()

    @classmethod
    def _delivery_summary(
        cls,
        deliveries: list[dict[str, Any]],
    ) -> dict[str, int]:
        return {
            "delivered_count": len(deliveries),
            "pending_count": sum(
                1
                for delivery in deliveries
                if cls._delivery_status(delivery) == "DELIVERED"
            ),
            "accepted_count": sum(
                1
                for delivery in deliveries
                if cls._delivery_status(delivery) == "ACCEPTED"
            ),
            "rejected_count": sum(
                1
                for delivery in deliveries
                if cls._delivery_status(delivery) == "REJECTED"
            ),
        }

    @staticmethod
    def _delivery_feedback_summary(
        feedback: list[dict[str, Any]],
    ) -> dict[str, int | float | None]:
        statuses = {
            "OPEN": 0,
            "ACKNOWLEDGED": 0,
            "RESOLVED": 0,
            "DISMISSED": 0,
        }
        ratings = []
        blocking_open = 0
        change_requests_open = 0
        for item in feedback:
            current_status = str(item.get("status") or "OPEN").upper()
            statuses[current_status] = statuses.get(current_status, 0) + 1
            rating = item.get("rating")
            if isinstance(rating, (int, float)):
                ratings.append(float(rating))
            is_open = current_status in {"OPEN", "ACKNOWLEDGED"}
            if is_open and str(item.get("severity") or "").upper() == "BLOCKER":
                blocking_open += 1
            if is_open and str(item.get("verdict") or "").upper() == "REQUEST_CHANGES":
                change_requests_open += 1
        unresolved = statuses.get("OPEN", 0) + statuses.get("ACKNOWLEDGED", 0)
        return {
            "total_count": len(feedback),
            "open_count": statuses.get("OPEN", 0),
            "acknowledged_count": statuses.get("ACKNOWLEDGED", 0),
            "resolved_count": statuses.get("RESOLVED", 0),
            "dismissed_count": statuses.get("DISMISSED", 0),
            "unresolved_count": unresolved,
            "blocking_open_count": blocking_open,
            "open_change_request_count": change_requests_open,
            "average_rating": round(sum(ratings) / len(ratings), 3) if ratings else None,
        }

    @staticmethod
    def _delivery_receipt_payload(delivery: dict[str, Any]) -> dict[str, Any]:
        return {
            key: value
            for key, value in delivery.items()
            if key != "receipt_path"
        }

    def _delivery_record(
        self,
        project: ProjectRuntime,
        delivery_id: str,
    ) -> dict[str, Any]:
        for delivery in project.deliveries:
            if delivery.get("delivery_id") == delivery_id:
                return delivery
        raise DeliveryNotFound(delivery_id)

    def _provenance_report(self, project_id: str) -> Path:
        return self.output_root / project_id / "provenance-report.json"

    def _compliance_report(self, project_id: str) -> Path:
        return self.output_root / project_id / "compliance-report.json"

    def _distribution_report(self, project_id: str) -> Path:
        return self.output_root / project_id / "distribution-report.json"

    def _closeout_report(self, project_id: str) -> Path:
        return self.output_root / project_id / "closeout-report.json"

    def _acceptance_report(self, project_id: str) -> Path:
        return self.output_root / project_id / "acceptance-report.json"

    def _retrospective_report(self, project_id: str) -> Path:
        return self.output_root / project_id / "retrospective-report.json"

    def _trace_report(self, project_id: str) -> Path:
        return self.output_root / project_id / "trace-report.json"

    def _continuity_report(self, project_id: str) -> Path:
        return self.output_root / project_id / "continuity-report.json"

    def _archive_package(self, project_id: str) -> Path:
        return self.output_root / project_id / f"{project_id}-archive.zip"

    def _archive_verification_report(self, project_id: str) -> Path:
        return self.output_root / project_id / "archive-verification.json"

    def _invalidate_postproduction(self, project: ProjectRuntime) -> None:
        self._invalidate_workflow_from(
            project,
            "postproduction",
            reason="postproduction_input_changed",
            actor="postproduction",
        )
        project.status = ProjectStatus.IN_PROGRESS if project.shots else ProjectStatus.DRAFT
        project.final_mp4 = None
        project.lipsync_artifact = None
        project.subtitle_srt = None
        project.delivery_package = None
        project.archive_package = None
        project.release = None

    def _subtitle_file(self, project_id: str) -> Path:
        return self.output_root / project_id / "final-subtitles.srt"

    def _write_project_subtitles(
        self,
        project: ProjectRuntime,
    ) -> tuple[Path, int, float]:
        cues: list[SubtitleCue] = []
        if project.dialogue_timeline and self._dialogue_timeline_current(project):
            lines = self._validated_dialogue_lines(
                project, project.dialogue_timeline["lines"]
            )
            cues = [
                SubtitleCue(
                    start_seconds=line.start_seconds,
                    end_seconds=line.end_seconds,
                    text=f"{line.speaker}: {line.text}",
                )
                for line in lines
            ]
            path = self._subtitle_file(project.brief.project_id)
            write_srt(path, cues)
            return path, len(cues), self._dialogue_duration(project)

        cursor = 0.0
        for runtime in project.shots.values():
            duration = float(runtime.shot.duration_seconds)
            text = runtime.shot.subtitle_text or runtime.shot.description
            cues.append(
                SubtitleCue(
                    start_seconds=cursor,
                    end_seconds=cursor + duration,
                    text=text,
                )
            )
            cursor += duration
        path = self._subtitle_file(project.brief.project_id)
        write_srt(path, cues)
        return path, len(cues), cursor

    def _ensure_project_subtitles(
        self,
        project: ProjectRuntime,
    ) -> tuple[Path, int, float]:
        path = Path(project.subtitle_srt) if project.subtitle_srt else self._subtitle_file(
            project.brief.project_id
        )
        if path.is_file():
            project.subtitle_srt = str(path)
            cue_count = sum(
                1
                for line in path.read_text(encoding="utf-8").splitlines()
                if line.isdigit()
            )
            duration = sum(
                float(runtime.shot.duration_seconds)
                for runtime in project.shots.values()
            )
            return path, cue_count, duration
        return self._write_project_subtitles(project)

    def export_audit_log(
        self,
        project_id: str,
        *,
        actor: str = "studio-user",
    ) -> dict[str, Any]:
        project = self._project(project_id)
        self._ensure_active(project)
        output_dir = self.output_root / project_id
        output_dir.mkdir(parents=True, exist_ok=True)
        self._record_event(
            project,
            action="audit.exported",
            actor=actor,
            message="Audit log exported.",
        )
        path = output_dir / "audit-log.json"
        payload = {
            **self.audit_log(project_id),
            "policy": self.policy_report(project_id),
            "cost": self._cost_report(project),
            "release": project.release,
        }
        path.write_text(
            json.dumps(payload, ensure_ascii=True, indent=2),
            encoding="utf-8",
        )
        self._persist()
        return {
            "project_id": project_id,
            "audit_log": str(path),
            "event_count": payload["count"],
        }

    def export_audit_csv(
        self,
        project_id: str,
        *,
        actor: str = "studio-user",
    ) -> dict[str, Any]:
        project = self._project(project_id)
        self._ensure_active(project)
        output_dir = self.output_root / project_id
        output_dir.mkdir(parents=True, exist_ok=True)
        self._record_event(
            project,
            action="audit.csv_exported",
            actor=actor,
            message="Audit log CSV exported.",
        )
        path, event_count = self._write_audit_csv_file(project_id)
        self._persist()
        return {
            "project_id": project_id,
            "audit_csv": str(path),
            "event_count": event_count,
        }

    def export_audit_integrity_report(
        self,
        project_id: str,
        *,
        actor: str = "studio-user",
    ) -> dict[str, Any]:
        project = self._project(project_id)
        self._ensure_active(project)
        output_dir = self.output_root / project_id
        output_dir.mkdir(parents=True, exist_ok=True)
        self._record_event(
            project,
            action="audit.integrity_exported",
            actor=actor,
            message="Audit integrity report exported.",
        )
        report = self.audit_integrity_report(project_id)
        path = output_dir / "audit-integrity.json"
        path.write_text(
            json.dumps(report, ensure_ascii=True, indent=2),
            encoding="utf-8",
        )
        self._persist()
        return {
            "project_id": project_id,
            "audit_integrity_report": str(path),
            "integrity": report,
        }

    def audit_anchor_report(self, project_id: str) -> dict[str, Any]:
        project = self._project(project_id)
        anchors = copy.deepcopy(project.audit_anchors)
        receipt_checks = [
            self.audit_anchors.verify_receipt(anchor)
            for anchor in anchors
        ]
        return {
            **self.audit_anchor_status(),
            "project_id": project_id,
            "anchor_count": len(anchors),
            "latest": anchors[-1] if anchors else None,
            "anchors": anchors,
            "verification": {
                "verified": bool(receipt_checks)
                and all(check["verified"] for check in receipt_checks),
                "verified_count": sum(
                    1 for check in receipt_checks if check["verified"]
                ),
                "checks": receipt_checks,
            },
            "current_integrity": self.audit_integrity_report(project_id),
        }

    def anchor_audit_chain(
        self,
        project_id: str,
        *,
        actor: str = "studio-user",
    ) -> dict[str, Any]:
        """Anchor a verified chain snapshot, then record the receipt in the native chain."""
        project = self._project(project_id)
        self._ensure_active(project)
        integrity = self.audit_integrity_report(project_id)
        if not integrity["verified"] or not integrity["head_hash"]:
            raise WorkflowError("a verified audit chain with at least one event is required before anchoring")
        try:
            receipt = self.audit_anchors.anchor(project_id, integrity)
        except AuditAnchorError as exc:
            raise WorkflowError(str(exc)) from exc
        project.audit_anchors.append(copy.deepcopy(receipt))
        self._record_event(
            project,
            action="audit.anchored",
            actor=actor,
            message="Audit chain head externally anchored.",
            details={
                "anchor_id": receipt["anchor_id"],
                "mode": receipt["mode"],
                "anchor_hash": receipt["anchor_hash"],
                "head_hash": receipt["head_hash"],
                "event_count": receipt["event_count"],
                "receipt_id": receipt.get("receipt_id"),
                "retention_until": receipt.get("retention_until"),
            },
        )
        output_dir = self.output_root / project_id
        output_dir.mkdir(parents=True, exist_ok=True)
        report = self.audit_anchor_report(project_id)
        path = output_dir / "audit-anchors.json"
        path.write_text(
            json.dumps(report, ensure_ascii=True, indent=2),
            encoding="utf-8",
        )
        self._persist()
        return {
            "project_id": project_id,
            "anchor": receipt,
            "audit_anchors_report": str(path),
            "anchors": report,
        }

    def _write_audit_csv_file(self, project_id: str) -> tuple[Path, int]:
        project = self._project(project_id)
        output_dir = self.output_root / project_id
        output_dir.mkdir(parents=True, exist_ok=True)
        events = [
            self._audit_event_view(event)
            for event in project.audit_events
        ]
        path = output_dir / "audit-log.csv"
        with path.open("w", encoding="utf-8", newline="") as stream:
            writer = csv.DictWriter(
                stream,
                fieldnames=[
                    "occurred_at",
                    "action",
                    "actor",
                    "shot_id",
                    "message",
                    "details_json",
                    "trace_id",
                    "sequence",
                    "previous_hash",
                    "event_hash",
                ],
            )
            writer.writeheader()
            for event in events:
                writer.writerow(
                    {
                        "occurred_at": event["occurred_at"],
                        "action": event["action"],
                        "actor": event["actor"],
                        "shot_id": event["shot_id"] or "",
                        "message": event["message"],
                        "details_json": json.dumps(
                            event["details"],
                            ensure_ascii=True,
                            sort_keys=True,
                        ),
                        "trace_id": event["trace_id"] or "",
                        "sequence": event["sequence"] or "",
                        "previous_hash": event["previous_hash"] or "",
                        "event_hash": event["event_hash"] or "",
                    }
                )
        return path, len(events)

    def latest_evaluation_report(self, project_id: str) -> dict[str, Any]:
        project = self._project(project_id)
        current = self._build_evaluation_report(project_id)
        self._attach_evaluation_baseline_gate(project, current)
        return {
            "project_id": project_id,
            "latest": project.evaluations[-1] if project.evaluations else None,
            "current": current,
            "history": project.evaluations,
            "baselines": copy.deepcopy(project.evaluation_baselines),
            "regression": copy.deepcopy(current.get("regression")),
        }

    @staticmethod
    def _model_evaluation_gates(report: dict[str, Any]) -> list[dict[str, Any]]:
        excluded = {"delivery_packaged", "release_recorded", "model_regression_baseline"}
        return [
            gate
            for gate in report.get("gates", [])
            if gate.get("name") not in excluded
        ]

    def _evaluation_model_context(self, project: ProjectRuntime) -> dict[str, Any]:
        workflow_templates = sorted(
            runtime.spec.workflow.template_id
            for runtime in project.shots.values()
        )
        provider_routes = sorted(
            {
                str((runtime.route or {}).get("provider") or self.provider_status.get("provider") or self.provider.name)
                for runtime in project.shots.values()
            }
        )
        prompts = self._active_prompt_snapshots(project)
        payload = {
            "provider_mode": self.provider_status.get("mode"),
            "provider_routes": provider_routes,
            "workflow_templates": workflow_templates,
            "prompt_snapshots": prompts,
        }
        return {**payload, "fingerprint": fingerprint(payload)}

    def _evaluation_baseline_report(
        self,
        project: ProjectRuntime,
        report: dict[str, Any],
    ) -> dict[str, Any]:
        active = [
            baseline
            for baseline in project.evaluation_baselines
            if baseline.get("active", True)
        ]
        context = self._evaluation_model_context(project)
        gate_status = {
            str(gate.get("name")): bool(gate.get("passed"))
            for gate in self._model_evaluation_gates(report)
        }
        score = float(report.get("model_quality_score") or 0.0)
        results = []
        for baseline in active:
            required = [str(name) for name in baseline.get("required_gates", [])]
            missing = [name for name in required if not gate_status.get(name, False)]
            expected_fingerprint = str((baseline.get("context") or {}).get("fingerprint") or "")
            context_passed = bool(expected_fingerprint) and expected_fingerprint == context["fingerprint"]
            minimum_score = float(baseline.get("minimum_score") or 0.0)
            score_passed = score >= minimum_score
            results.append(
                {
                    "baseline_id": baseline.get("baseline_id"),
                    "name": baseline.get("name"),
                    "passed": context_passed and score_passed and not missing,
                    "context_passed": context_passed,
                    "score_passed": score_passed,
                    "required_gates_passed": not missing,
                    "missing_gates": missing,
                    "observed_score": score,
                    "minimum_score": minimum_score,
                    "expected_context_fingerprint": expected_fingerprint or None,
                    "observed_context_fingerprint": context["fingerprint"],
                }
            )
        return {
            "schema_version": "mediaforge-evaluation-baseline-check-v1",
            "active_count": len(active),
            "passed": all(result["passed"] for result in results),
            "model_context": context,
            "results": results,
        }

    def _attach_evaluation_baseline_gate(
        self,
        project: ProjectRuntime,
        report: dict[str, Any],
    ) -> None:
        regression = self._evaluation_baseline_report(project, report)
        report["regression"] = regression
        if not regression["active_count"]:
            return
        report["gates"].append(
            {
                "name": "model_regression_baseline",
                "label": "Model admission baseline passes",
                "passed": regression["passed"],
                "weight": 0,
                "observed": regression["results"],
                "expected": "active baseline model context, quality score and gates",
            }
        )
        failed = [gate for gate in report["gates"] if not gate["passed"]]
        report["passed"] = not failed
        report["gate_count"] = len(report["gates"])
        report["failed_gate_count"] = len(failed)
        report["recommendations"] = [
            f"{gate['label']}: expected {gate['expected']}, observed {gate['observed']}"
            for gate in failed
        ]

    def create_evaluation_baseline(
        self,
        project_id: str,
        *,
        name: str,
        minimum_score: float | None = None,
        actor: str = "quality-owner",
    ) -> dict[str, Any]:
        project = self._project(project_id)
        self._ensure_active(project)
        if project.release is not None:
            raise WorkflowError("released project evaluation baseline cannot be changed")
        clean_name = str(name or "").strip()
        if not clean_name or len(clean_name) > 240:
            raise WorkflowError("evaluation baseline name must contain 1 to 240 characters")
        report = self._build_evaluation_report(project_id)
        model_gates = self._model_evaluation_gates(report)
        failed_model_gates = [gate["name"] for gate in model_gates if not gate["passed"]]
        if failed_model_gates:
            raise WorkflowError(
                "evaluation baseline requires passing model gates: "
                + ", ".join(failed_model_gates)
            )
        observed_score = float(report["model_quality_score"])
        requested_score = observed_score if minimum_score is None else float(minimum_score)
        if not 0 <= requested_score <= 1:
            raise WorkflowError("evaluation baseline minimum_score must be between 0 and 1")
        if requested_score > observed_score:
            raise WorkflowError("evaluation baseline minimum_score cannot exceed the observed model score")
        for baseline in project.evaluation_baselines:
            baseline["active"] = False
        baseline = {
            "baseline_id": f"baseline_{uuid4().hex[:16]}",
            "name": clean_name,
            "active": True,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "actor": actor.strip() or "quality-owner",
            "minimum_score": round(requested_score, 4),
            "observed_score": round(observed_score, 4),
            "required_gates": [gate["name"] for gate in model_gates],
            "context": self._evaluation_model_context(project),
        }
        project.evaluation_baselines.append(baseline)
        self._record_event(
            project,
            action="evaluation.baseline_created",
            actor=baseline["actor"],
            message=f"Evaluation baseline {baseline['baseline_id']} created.",
            details={
                "baseline_id": baseline["baseline_id"],
                "minimum_score": baseline["minimum_score"],
                "context_fingerprint": baseline["context"]["fingerprint"],
            },
        )
        self._persist()
        return {
            "project_id": project_id,
            "baseline": copy.deepcopy(baseline),
            "regression": self._evaluation_baseline_report(project, report),
        }

    def run_project_evaluation(
        self,
        project_id: str,
        *,
        actor: str = "quality-evaluator",
    ) -> dict[str, Any]:
        project = self._project(project_id)
        self._ensure_active(project)
        output_dir = self.output_root / project_id
        output_dir.mkdir(parents=True, exist_ok=True)
        path = output_dir / "evaluation-report.json"
        report = {
            **self._build_evaluation_report(project_id),
            "evaluation_id": f"evaluation_{uuid4().hex[:16]}",
            "report_path": str(path),
        }
        self._attach_evaluation_baseline_gate(project, report)
        project.evaluations.append(report)
        self._record_event(
            project,
            action="project.evaluated",
            actor=actor,
            message=(
                f"Delivery evaluation scored "
                f"{int(round(report['score'] * 100))}/100."
            ),
            details={
                "score": report["score"],
                "passed": report["passed"],
                "failed_gates": [
                    gate["name"]
                    for gate in report["gates"]
                    if not gate["passed"]
                ],
            },
        )
        path.write_text(
            json.dumps(report, ensure_ascii=True, indent=2),
            encoding="utf-8",
        )
        self._persist()
        return {
            "project_id": project_id,
            "evaluation_report": str(path),
            "report": report,
            "history_count": len(project.evaluations),
        }

    def latest_provider_benchmark(self, project_id: str) -> dict[str, Any]:
        project = self._project(project_id)
        current = self._build_provider_benchmark(project_id)
        return {
            "project_id": project_id,
            "latest": (
                project.provider_benchmarks[-1]
                if project.provider_benchmarks
                else None
            ),
            "current": current,
            "history": project.provider_benchmarks,
        }

    def run_provider_benchmark(
        self,
        project_id: str,
        *,
        actor: str = "studio-user",
    ) -> dict[str, Any]:
        project = self._project(project_id)
        self._ensure_active(project)
        output_dir = self.output_root / project_id
        output_dir.mkdir(parents=True, exist_ok=True)
        path = output_dir / "provider-benchmark.json"
        report = {
            **self._build_provider_benchmark(project_id),
            "report_path": str(path),
        }
        project.provider_benchmarks.append(report)
        self._record_event(
            project,
            action="provider.benchmarked",
            actor=actor,
            message="Provider benchmark report generated.",
            details={
                "provider_count": report["provider_count"],
                "recommended_provider": report["recommended_provider"],
            },
        )
        path.write_text(
            json.dumps(report, ensure_ascii=True, indent=2),
            encoding="utf-8",
        )
        self._persist()
        return {
            "project_id": project_id,
            "benchmark_report": str(path),
            "benchmark": report,
            "history_count": len(project.provider_benchmarks),
        }

    def release_project(
        self,
        project_id: str,
        *,
        channel: str = "internal-review",
        comment: str = "",
        actor: str = "studio-publisher",
    ) -> dict[str, Any]:
        project = self._project(project_id)
        self._ensure_active(project)
        if project.status != ProjectStatus.EXPORTED or not project.final_mp4:
            raise WorkflowError("export project before release")
        if not project.delivery_package:
            raise WorkflowError("package delivery before release")
        self._require_production_stage_lock(project, "delivery")
        self._require_release_content_credentials(project)
        package_verification = self.verify_delivery_package(
            project_id,
            actor="release-gate",
        )
        if not package_verification["verification"]["passed"]:
            raise WorkflowError("delivery package verification failed")
        policy = self.policy_report(project_id)
        if not policy["passed"]:
            raise WorkflowError("project policy report is not releasable")
        compliance = self.project_compliance(project_id)
        if not compliance["passed"]:
            failed_checks = ", ".join(
                check["name"]
                for check in compliance["checks"]
                if check["blocking"] and not check["passed"]
            )
            raise WorkflowError(
                "project compliance report is not releasable"
                f": {failed_checks}"
            )
        continuity = self.project_continuity(project_id)
        if not continuity["passed"]:
            failed_checks = ", ".join(
                check["name"]
                for check in continuity["checks"]
                if not check["passed"]
            )
            raise WorkflowError(
                "project continuity report is not releasable"
                f": {failed_checks}"
            )
        continuity_report = self._continuity_report(project_id)
        continuity_report.write_text(
            json.dumps(continuity, ensure_ascii=True, indent=2),
            encoding="utf-8",
        )
        pre_release_evaluation = self._build_evaluation_report(project_id)
        admission = self._evaluation_baseline_report(project, pre_release_evaluation)
        if admission["active_count"] and not admission["passed"]:
            failed_baselines = ", ".join(
                str(item.get("baseline_id"))
                for item in admission["results"]
                if not item["passed"]
            )
            raise WorkflowError(
                "model admission baseline did not pass before release: "
                + failed_baselines
            )
        if project.release is None:
            timestamp = datetime.now(timezone.utc)
            project.release = {
                "release_id": f"release_{timestamp.strftime('%Y%m%d%H%M%S')}",
                "status": "RELEASED",
                "channel": channel,
                "comment": comment,
                "actor": actor,
                "released_at": timestamp.isoformat(),
                "final_mp4": project.final_mp4,
                "audio_track": project.audio_track,
                "audio_track_metadata": copy.deepcopy(project.audio_track_metadata),
                "delivery_package": project.delivery_package,
                "package_verified": True,
                "package_verification_report": package_verification[
                    "verification_report"
                ],
                "policy_version": policy["policy_version"],
                "policy_passed": policy["passed"],
                "compliance_version": compliance["schema_version"],
                "compliance_passed": compliance["passed"],
                "continuity_version": continuity["schema_version"],
                "continuity_passed": continuity["passed"],
                "cost": self._cost_report(project),
            }
            self._record_event(
                project,
                action="project.released",
                actor=actor,
                message=f"Project released to {channel}.",
                details={"release": project.release},
            )
            if not project.provider_benchmarks:
                self.run_provider_benchmark(project_id, actor="release-gate")
            self.run_project_evaluation(project_id, actor="release-gate")
            refreshed_package = self.build_delivery_package(project_id)
        else:
            refreshed_package = {
                "project_id": project_id,
                "package_zip": project.delivery_package,
            }
        return {
            "project_id": project_id,
            "release": project.release,
            "package": refreshed_package,
        }

    def project_view(self, project_id: str) -> dict[str, Any]:
        project = self._project(project_id)
        delivery_verified, verification_report = self._delivery_verification_state(
            project
        )
        provenance_report = self._provenance_report(project_id)
        compliance = self.project_compliance(project_id)
        compliance_report = self._compliance_report(project_id)
        continuity = self.project_continuity(project_id)
        continuity_report = self._continuity_report(project_id)
        distribution_report = self._distribution_report(project_id)
        acceptance_report = self._acceptance_report(project_id)
        closeout_report = self._closeout_report(project_id)
        retrospective_report = self._retrospective_report(project_id)
        trace_report = self._trace_report(project_id)
        archive_verified, archive_verification_report = (
            self._archive_verification_state(project)
        )
        dialogue_timeline = copy.deepcopy(project.dialogue_timeline)
        if dialogue_timeline:
            dialogue_timeline["current"] = self._dialogue_timeline_current(project)
        return {
            "project_id": project.brief.project_id,
            "trace_id": project.trace_id,
            "status": project.status,
            "archived": project.archived_at is not None,
            "archived_at": (
                project.archived_at.isoformat()
                if project.archived_at
                else None
            ),
            "brief": project.brief.model_dump(mode="json"),
            "story_bible": project.story_bible,
            "source_documents": [
                self._source_document_view(document)
                for document in project.source_documents
            ],
            "source_chapters": [
                self._source_chapter_view(chapter)
                for chapter in self._ordered_source_chapters(project)
            ],
            "narrative_event_candidates": [
                self._narrative_event_candidate_view(candidate)
                for candidate in self._ordered_narrative_event_candidates(project)
            ],
            "narrative_events": [
                self._narrative_event_view(event)
                for event in self._ordered_narrative_events(project)
            ],
            "adaptation_scenes": [
                self._adaptation_scene_view(scene)
                for scene in self._ordered_adaptation_scenes(project)
            ],
            "reference_assets": self.asset_inventory(project_id)["reference_assets"],
            "cost": self._cost_report(project),
            "policy": self.policy_report(project_id),
            "shots": [
                self.shot_view(runtime)
                for runtime in project.shots.values()
            ],
            "final_mp4": project.final_mp4,
            "lipsync_artifact": copy.deepcopy(project.lipsync_artifact),
            "subtitle_srt": project.subtitle_srt,
            "audio_track": project.audio_track,
            "audio_track_metadata": copy.deepcopy(project.audio_track_metadata),
            "dialogue_timeline": dialogue_timeline,
            "edit_timeline": self.edit_timeline(project_id),
            "content_credentials": self.project_content_credentials(project_id),
            "prompt_version_count": len(project.prompt_versions),
            "active_prompt_count": sum(
                1 for prompt in project.prompt_versions if prompt.get("status") == "ACTIVE"
            ),
            "evaluation_annotation_count": len(project.evaluation_annotations),
            "experiment_count": len(project.experiments),
            "delivery_package": project.delivery_package,
            "delivery_verified": delivery_verified,
            "delivery_verification_report": (
                str(verification_report) if delivery_verified else None
            ),
            "provenance_report": (
                str(provenance_report) if provenance_report.is_file() else None
            ),
            "compliance_passed": compliance["passed"],
            "compliance_report": (
                str(compliance_report) if compliance_report.is_file() else None
            ),
            "continuity": continuity,
            "continuity_passed": continuity["passed"],
            "continuity_report": (
                str(continuity_report) if continuity_report.is_file() else None
            ),
            "release": project.release,
            "delivery_count": len(project.deliveries),
            "delivery_feedback": self._delivery_feedback_summary(project.delivery_feedback),
            "delivery_feedback_gate": self.delivery_feedback_gate(project.brief.project_id),
            "latest_delivery": (
                project.deliveries[-1] if project.deliveries else None
            ),
            "distribution_report": (
                str(distribution_report) if distribution_report.is_file() else None
            ),
            "acceptance_report": (
                str(acceptance_report) if acceptance_report.is_file() else None
            ),
            "closeout_report": (
                str(closeout_report) if closeout_report.is_file() else None
            ),
            "retrospective_report": (
                str(retrospective_report)
                if retrospective_report.is_file()
                else None
            ),
            "trace_report": (
                str(trace_report) if trace_report.is_file() else None
            ),
            "archive_package": project.archive_package,
            "archive_verified": archive_verified,
            "archive_verification_report": (
                str(archive_verification_report) if archive_verified else None
            ),
            "latest_evaluation": (
                project.evaluations[-1] if project.evaluations else None
            ),
            "latest_provider_benchmark": (
                project.provider_benchmarks[-1]
                if project.provider_benchmarks
                else None
            ),
            "comparison_count": len(project.comparison_reports),
            "latest_comparison": (
                project.comparison_reports[-1]
                if project.comparison_reports
                else None
            ),
            "collaboration": self.project_collaboration(project_id),
            "member_count": len(project.members),
            "comment_count": len(project.comments),
            "content_credential_count": len(project.content_credentials),
            "production_workflow": self.production_workflow(project_id),
            "audit_events": [
                self._audit_event_view(event)
                for event in project.audit_events
            ],
            "audit_integrity": self.audit_integrity_report(project_id),
            "import_evidence": copy.deepcopy(project.import_evidence),
            "created_at": project.created_at.isoformat(),
        }

    def project_summary(self, project: ProjectRuntime) -> dict[str, Any]:
        shots = list(project.shots.values())
        delivery_verified, verification_report = self._delivery_verification_state(
            project
        )
        provenance_report = self._provenance_report(project.brief.project_id)
        compliance = self.project_compliance(project.brief.project_id)
        compliance_report = self._compliance_report(project.brief.project_id)
        continuity = self.project_continuity(project.brief.project_id)
        continuity_report = self._continuity_report(project.brief.project_id)
        distribution_report = self._distribution_report(project.brief.project_id)
        acceptance_report = self._acceptance_report(project.brief.project_id)
        closeout_report = self._closeout_report(project.brief.project_id)
        retrospective_report = self._retrospective_report(project.brief.project_id)
        trace_report = self._trace_report(project.brief.project_id)
        archive_verified, archive_verification_report = (
            self._archive_verification_state(project)
        )
        approved = sum(
            1
            for runtime in shots
            if runtime.review_status == ReviewStatus.APPROVED
        )
        generated = sum(1 for runtime in shots if runtime.current_artifact)
        revisions = sum(runtime.revision for runtime in shots)
        variants = sum(len(runtime.variants) for runtime in shots)
        return {
            "project_id": project.brief.project_id,
            "trace_id": project.trace_id,
            "title": project.brief.title,
            "status": project.status,
            "archived": project.archived_at is not None,
            "archived_at": (
                project.archived_at.isoformat()
                if project.archived_at
                else None
            ),
            "created_at": project.created_at.isoformat(),
            "shot_count": len(shots),
            "generated_shots": generated,
            "approved_shots": approved,
            "revision_count": revisions,
            "variant_count": variants,
            "comparison_count": len(project.comparison_reports),
            "policy_warning_count": sum(
                int(report.get("warnings") or 0)
                for report in project.policy_reports
            ),
            "budget": project.brief.budget,
            "spent": self._cost_report(project)["spent"],
            "final_mp4": project.final_mp4,
            "subtitle_srt": project.subtitle_srt,
            "audio_track": project.audio_track,
            "audio_track_metadata": copy.deepcopy(project.audio_track_metadata),
            "dialogue_timeline": copy.deepcopy(project.dialogue_timeline),
            "edit_timeline_revision": int(
                (project.edit_timeline or {}).get("revision", 0)
            ),
            "prompt_version_count": len(project.prompt_versions),
            "evaluation_annotation_count": len(project.evaluation_annotations),
            "experiment_count": len(project.experiments),
            "content_credential_count": len(project.content_credentials),
            "delivery_package": project.delivery_package,
            "delivery_verified": delivery_verified,
            "delivery_verification_report": (
                str(verification_report) if delivery_verified else None
            ),
            "provenance_report": (
                str(provenance_report) if provenance_report.is_file() else None
            ),
            "compliance_passed": compliance["passed"],
            "compliance_report": (
                str(compliance_report) if compliance_report.is_file() else None
            ),
            "continuity_passed": continuity["passed"],
            "continuity_report": (
                str(continuity_report) if continuity_report.is_file() else None
            ),
            "released": project.release is not None,
            "release": project.release,
            "delivery_count": len(project.deliveries),
            "delivery_feedback": self._delivery_feedback_summary(project.delivery_feedback),
            "delivery_feedback_gate": self.delivery_feedback_gate(project.brief.project_id),
            "latest_delivery": (
                project.deliveries[-1] if project.deliveries else None
            ),
            "distribution_report": (
                str(distribution_report) if distribution_report.is_file() else None
            ),
            "acceptance_report": (
                str(acceptance_report) if acceptance_report.is_file() else None
            ),
            "closeout_report": (
                str(closeout_report) if closeout_report.is_file() else None
            ),
            "retrospective_report": (
                str(retrospective_report)
                if retrospective_report.is_file()
                else None
            ),
            "trace_report": (
                str(trace_report) if trace_report.is_file() else None
            ),
            "archive_package": project.archive_package,
            "archive_verified": archive_verified,
            "archive_verification_report": (
                str(archive_verification_report) if archive_verified else None
            ),
            "evaluation_score": (
                project.evaluations[-1]["score"]
                if project.evaluations
                else None
            ),
        }

    def cost_report(self, project_id: str) -> dict[str, Any]:
        return self._cost_report(self._project(project_id))

    def project_jobs(
        self,
        project_id: str,
        *,
        status: JobStatus | None = None,
        shot_id: str | None = None,
        limit: int = 200,
    ) -> dict[str, Any]:
        self._project(project_id)
        all_jobs = [
            job
            for job in self.jobs.all()
            if job.spec.project_id == project_id
        ]
        filtered_jobs = [
            job
            for job in all_jobs
            if (status is None or job.status == status)
            and (shot_id is None or job.spec.shot_id == shot_id)
        ]
        jobs = [
            self._job_view(job)
            for job in filtered_jobs[:limit]
        ]
        return {
            "project_id": project_id,
            "count": len(jobs),
            "total_count": len(filtered_jobs),
            "filters": {
                "status": status,
                "shot_id": shot_id,
                "limit": limit,
            },
            "jobs": jobs,
        }

    def project_shots(
        self,
        project_id: str,
        *,
        review_status: ReviewStatus | None = None,
        query: str | None = None,
        limit: int = 200,
    ) -> dict[str, Any]:
        project = self._project(project_id)
        normalized_query = (query or "").strip().lower()
        all_shots = list(project.shots.values())
        filtered = [
            runtime
            for runtime in all_shots
            if (
                review_status is None
                or runtime.review_status == review_status
            )
            and (
                not normalized_query
                or normalized_query in " ".join(
                    [
                        runtime.shot.shot_id,
                        runtime.shot.scene,
                        runtime.shot.description,
                        runtime.shot.mood,
                    ]
                ).lower()
            )
        ]
        return {
            "project_id": project_id,
            "count": min(len(filtered), limit),
            "total_count": len(filtered),
            "shots": [
                self.shot_view(runtime)
                for runtime in filtered[:limit]
            ],
        }

    def cancel_job(
        self,
        project_id: str,
        job_id: str,
        *,
        actor: str = "studio-user",
    ) -> dict[str, Any]:
        project = self._project(project_id)
        self._ensure_active(project)
        try:
            job = self.jobs.get(job_id)
        except KeyError as exc:
            raise JobNotFound(job_id) from exc
        if job.spec.project_id != project_id:
            raise JobNotFound(job_id)
        if job.status in {
            JobStatus.SUCCEEDED,
            JobStatus.CANCELED,
        } or (
            job.status in {
                JobStatus.FAILED,
                JobStatus.QUALITY_REJECTED,
            }
            and job.retry_at is None
        ):
            raise WorkflowError(f"job is already terminal: {job.status}")
        if job.status in {
            JobStatus.FAILED,
            JobStatus.QUALITY_REJECTED,
        }:
            self.jobs.transition(
                job.job_id,
                JobStatus.RETRY_WAIT,
                reason="scheduled retry canceled",
            )
        self.jobs.transition(
            job.job_id,
            JobStatus.CANCELED,
            reason="manual cancel requested",
        )
        self._release_job_lease(job)
        self._record_event(
            project,
            action="job.canceled",
            actor=actor,
            message=f"{job.spec.shot_id} job canceled.",
            shot_id=job.spec.shot_id,
            details={"job_id": job.job_id, "status": job.status},
        )
        self._persist()
        return self._job_view(job)

    def operations_summary(self, project_id: str) -> dict[str, Any]:
        project = self._project(project_id)
        project_jobs = [
            job
            for job in self.jobs.all()
            if job.spec.project_id == project_id
        ]
        job_counts = {status.value: 0 for status in JobStatus}
        for job in project_jobs:
            job_counts[job.status.value] += 1
        delivery_verified, verification_report = self._delivery_verification_state(
            project
        )
        compliance = self.project_compliance(project_id)
        deliveries = project.deliveries
        latest_delivery = deliveries[-1] if deliveries else None
        latest_delivery_status = self._delivery_status(latest_delivery)
        delivery_summary = self._delivery_summary(deliveries)

        pending_shots = [
            runtime.shot.shot_id
            for runtime in project.shots.values()
            if runtime.current_artifact is None
            and runtime.review_status != ReviewStatus.CHANGES_REQUESTED
        ]
        changes_requested_shots = [
            runtime.shot.shot_id
            for runtime in project.shots.values()
            if runtime.review_status == ReviewStatus.CHANGES_REQUESTED
        ]
        ready_for_review_shots = [
            runtime.shot.shot_id
            for runtime in project.shots.values()
            if runtime.current_artifact
            and runtime.review_status == ReviewStatus.PENDING
        ]
        approved_shots = [
            runtime.shot.shot_id
            for runtime in project.shots.values()
            if runtime.review_status == ReviewStatus.APPROVED
        ]
        failed_jobs = [
            self._job_view(job)
            for job in project_jobs
            if job.status in {JobStatus.FAILED, JobStatus.QUALITY_REJECTED}
        ]
        retry_due_jobs = [
            job
            for job in self.jobs.due_retries()
            if job.spec.project_id == project_id
        ]
        next_retry_at = min(
            (
                job.retry_at
                for job in project_jobs
                if job.retry_at is not None
                and job.status in {
                    JobStatus.FAILED,
                    JobStatus.QUALITY_REJECTED,
                    JobStatus.RETRY_WAIT,
                }
            ),
            default=None,
        )
        queued_jobs = [
            self._job_view(job)
            for job in project_jobs
            if job.status in {
                JobStatus.VALIDATED,
                JobStatus.QUEUED,
                JobStatus.ADMITTED,
            }
        ]
        queued_shots = [
            job["spec"]["shot_id"]
            for job in queued_jobs
        ]
        retry_wait_jobs = [
            self._job_view(job)
            for job in project_jobs
            if job.retry_at is not None
            and job.status in {
                JobStatus.FAILED,
                JobStatus.QUALITY_REJECTED,
                JobStatus.RETRY_WAIT,
            }
        ]
        retry_wait_shots = [
            job["spec"]["shot_id"]
            for job in retry_wait_jobs
        ]
        stale_jobs = self._stale_jobs_for_project(project_id)

        archive_verified, archive_verification_report = (
            self._archive_verification_state(project)
        )

        if project.archived_at is not None:
            if (
                self._acceptance_report(project_id).is_file()
                or self._closeout_report(project_id).is_file()
            ):
                if not project.archive_package:
                    next_action = {
                        "code": "ARCHIVE_PACKAGE",
                        "label": "Build archive bundle",
                        "shot_ids": [],
                    }
                elif not archive_verified:
                    next_action = {
                        "code": "VERIFY_ARCHIVE",
                        "label": "Verify archive bundle",
                        "shot_ids": [],
                    }
                else:
                    next_action = {
                        "code": "CLOSED",
                        "label": "Project closed",
                        "shot_ids": [],
                    }
            else:
                next_action = {
                    "code": "RESTORE",
                    "label": "Restore project",
                    "shot_ids": [],
                }
        elif not project.shots:
            next_action = {
                "code": "PLAN",
                "label": "Plan story",
                "shot_ids": [],
            }
        elif stale_jobs:
            next_action = {
                "code": "RECOVER_STALE",
                "label": "Recover stale jobs",
                "shot_ids": [job["spec"]["shot_id"] for job in stale_jobs],
            }
        elif queued_jobs:
            next_action = {
                "code": "PROCESS_QUEUE",
                "label": "Run queued jobs",
                "shot_ids": queued_shots,
            }
        elif retry_wait_jobs:
            next_action = {
                "code": "RETRY_FAILED",
                "label": "Retry scheduled jobs",
                "shot_ids": retry_wait_shots,
            }
        elif failed_jobs:
            next_action = {
                "code": "RETRY_FAILED",
                "label": "Retry failed jobs",
                "shot_ids": [
                    job["spec"]["shot_id"]
                    for job in failed_jobs
                ],
            }
        elif changes_requested_shots:
            next_action = {
                "code": "RUN_REVISIONS",
                "label": "Run requested revisions",
                "shot_ids": changes_requested_shots,
            }
        elif pending_shots:
            next_action = {
                "code": "GENERATE_SHOTS",
                "label": "Generate pending shots",
                "shot_ids": pending_shots,
            }
        elif ready_for_review_shots:
            next_action = {
                "code": "REVIEW_SHOTS",
                "label": "Review ready shots",
                "shot_ids": ready_for_review_shots,
            }
        elif len(approved_shots) < len(project.shots):
            next_action = {
                "code": "REVIEW_SHOTS",
                "label": "Review shots",
                "shot_ids": [],
            }
        elif project.status != ProjectStatus.EXPORTED:
            next_action = {
                "code": "EXPORT",
                "label": "Export final sample",
                "shot_ids": [],
            }
        elif not project.delivery_package:
            next_action = {
                "code": "PACKAGE",
                "label": "Package delivery",
                "shot_ids": [],
            }
        elif not delivery_verified:
            next_action = {
                "code": "VERIFY_PACKAGE",
                "label": "Verify package",
                "shot_ids": [],
            }
        elif not compliance["passed"]:
            next_action = {
                "code": "CHECK_COMPLIANCE",
                "label": "Resolve compliance gate",
                "shot_ids": [],
            }
        elif not project.release:
            next_action = {
                "code": "RELEASE",
                "label": "Release project",
                "shot_ids": [],
            }
        elif not deliveries:
            next_action = {
                "code": "DISTRIBUTE",
                "label": "Record delivery",
                "shot_ids": [],
            }
        elif latest_delivery_status == "DELIVERED":
            next_action = {
                "code": "ACKNOWLEDGE_DELIVERY",
                "label": "Acknowledge delivery",
                "shot_ids": [],
            }
        elif latest_delivery_status == "ACCEPTED":
            next_action = {
                "code": "CLOSEOUT",
                "label": "Close project",
                "shot_ids": [],
            }
        elif latest_delivery_status == "REJECTED":
            next_action = {
                "code": "REDISTRIBUTE",
                "label": "Re-record delivery",
                "shot_ids": [],
            }
        else:
            next_action = {
                "code": "COMPLETE",
                "label": "Release complete",
                "shot_ids": [],
            }

        return {
            "project_id": project_id,
            "project_status": project.status,
            "archived": project.archived_at is not None,
            "archived_at": (
                project.archived_at.isoformat()
                if project.archived_at
                else None
            ),
            "released": project.release is not None,
            "release": project.release,
            "delivery_count": len(project.deliveries),
            "latest_delivery": (
                project.deliveries[-1] if project.deliveries else None
            ),
            "member_count": len(project.members),
            "comment_count": len(project.comments),
            "delivery_verified": delivery_verified,
            "delivery_verification_report": (
                str(verification_report) if delivery_verified else None
            ),
            "archive_package": project.archive_package,
            "archive_verified": archive_verified,
            "archive_verification_report": (
                str(archive_verification_report) if archive_verified else None
            ),
            "compliance_passed": compliance["passed"],
            "compliance": compliance,
            "delivery": delivery_summary,
            "job_counts": job_counts,
            "job_count": len(project_jobs),
            "failed_jobs": failed_jobs,
            "retry_wait_jobs": retry_wait_jobs,
            "retry_due_jobs": [
                self._job_view(job) for job in retry_due_jobs
            ],
            "retry": {
                "max_attempts": self.retry_policy.max_attempts,
                "base_delay_seconds": self.retry_policy.base_delay_seconds,
                "scheduled_jobs": len(retry_wait_jobs),
                "due_jobs": len(retry_due_jobs),
                "next_retry_at": (
                    next_retry_at.isoformat() if next_retry_at else None
                ),
            },
            "queued_jobs": queued_jobs,
            "queued_shots": queued_shots,
            "stale_jobs": stale_jobs,
            "queue": self._queue_summary(project_id),
            "pending_shots": pending_shots,
            "changes_requested_shots": changes_requested_shots,
            "ready_for_review_shots": ready_for_review_shots,
            "approved_shots": approved_shots,
            "generated_shots": sum(
                1
                for runtime in project.shots.values()
                if runtime.current_artifact
            ),
            "next_action": next_action,
            "policy": self.policy_report(project_id),
            "cost": self._cost_report(project),
            "latest_evaluation": (
                project.evaluations[-1] if project.evaluations else None
            ),
            "latest_provider_benchmark": (
                project.provider_benchmarks[-1]
                if project.provider_benchmarks
                else None
            ),
            "workflow": self.production_workflow(project_id),
        }

    @staticmethod
    def stage_locking_required() -> bool:
        return os.getenv("MEDIAFORGE_REQUIRE_STAGE_LOCKS", "false").strip().lower() in {
            "1",
            "true",
            "yes",
            "on",
        }

    @staticmethod
    def release_content_credentials_required() -> bool:
        return os.getenv(
            "MEDIAFORGE_REQUIRE_RELEASE_CONTENT_CREDENTIALS", "false"
        ).strip().lower() in {"1", "true", "yes", "on"}

    def _require_release_content_credentials(self, project: ProjectRuntime) -> None:
        if not self.release_content_credentials_required():
            return
        credentials = self.project_content_credentials(project.brief.project_id)
        final_media = credentials.get("summary", {}).get("final_media", {})
        if not isinstance(final_media, dict) or not final_media.get("ready"):
            raise WorkflowError(
                "current final MP4 requires an independently verified C2PA content credential before release"
            )

    def _stage_fingerprint(self, project: ProjectRuntime, stage_key: str) -> str:
        """Fingerprint only the evidence owned by one production stage."""
        shots = list(project.shots.values())
        if stage_key == "script":
            evidence: Any = {
                "brief": project.brief.model_dump(mode="json"),
                "story_bible": project.story_bible,
                "narrative_events": [event.model_dump(mode="json") for event in project.narrative_events],
                "adaptation_scenes": [scene.model_dump(mode="json") for scene in project.adaptation_scenes],
            }
        elif stage_key == "storyboard":
            evidence = [
                {"shot": runtime.shot.model_dump(mode="json"), "revision": runtime.revision}
                for runtime in shots
            ]
        elif stage_key == "assets":
            evidence = {
                "reference_assets": project.reference_assets,
                "specifications": [runtime.spec.model_dump(mode="json") for runtime in shots],
            }
        elif stage_key == "video":
            evidence = [
                {
                    "shot_id": runtime.shot.shot_id,
                    "revision": runtime.revision,
                    "artifact": runtime.current_artifact,
                    "quality": runtime.quality,
                    "review_status": runtime.review_status.value,
                }
                for runtime in shots
            ]
        elif stage_key == "postproduction":
            evidence = {
                "final_mp4": project.final_mp4,
                "subtitle_srt": project.subtitle_srt,
                "audio_track": project.audio_track,
                "audio_track_metadata": project.audio_track_metadata,
                "dialogue_timeline": project.dialogue_timeline,
                "edit_timeline": project.edit_timeline,
            }
        elif stage_key == "delivery":
            evidence = {
                "delivery_package": project.delivery_package,
            }
        else:
            raise WorkflowError(f"unknown production stage: {stage_key}")
        encoded = json.dumps(evidence, ensure_ascii=True, sort_keys=True, default=str)
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()

    @staticmethod
    def _workflow_gate(code: str, label: str, passed: bool, detail: str) -> dict[str, Any]:
        return {
            "code": code,
            "label": label,
            "passed": passed,
            "detail": detail,
        }

    def _workflow_stage_gates(
        self,
        project: ProjectRuntime,
        stage_key: str,
    ) -> list[dict[str, Any]]:
        shots = list(project.shots.values())
        shot_count = len(shots)
        generated_count = sum(1 for runtime in shots if runtime.current_artifact)
        approved_count = sum(
            1 for runtime in shots if runtime.review_status == ReviewStatus.APPROVED
        )
        quality_count = sum(
            1 for runtime in shots if runtime.quality and runtime.quality.get("passed")
        )
        current_specs = sum(1 for runtime in shots if runtime.spec is not None)
        asset_count = len(project.reference_assets)
        final_exists = bool(project.final_mp4 and Path(project.final_mp4).is_file())
        subtitle_exists = bool(project.subtitle_srt and Path(project.subtitle_srt).is_file())
        package_exists = bool(
            project.delivery_package and Path(project.delivery_package).is_file()
        )
        delivery_verified, _ = self._delivery_verification_state(project)

        if stage_key == "script":
            return [
                self._workflow_gate(
                    "story_bible",
                    "故事圣经与制作简报",
                    project.story_bible is not None,
                    "已生成" if project.story_bible else "需要先生成计划",
                ),
                self._workflow_gate(
                    "brief",
                    "项目简报",
                    bool(project.brief.premise and project.brief.characters),
                    f"{len(project.brief.characters)} 个角色",
                ),
            ]
        if stage_key == "storyboard":
            planned_duration = sum(runtime.shot.duration_seconds for runtime in shots)
            return [
                self._workflow_gate(
                    "shots",
                    "镜头卡",
                    shot_count > 0,
                    f"{shot_count} 个镜头",
                ),
                self._workflow_gate(
                    "duration",
                    "时长匹配",
                    shot_count > 0 and planned_duration == project.brief.duration_seconds,
                    f"{planned_duration}/{project.brief.duration_seconds} 秒",
                ),
            ]
        if stage_key == "assets":
            return [
                self._workflow_gate(
                    "shot_specifications",
                    "镜头资产规格",
                    shot_count > 0 and current_specs == shot_count,
                    f"{current_specs}/{shot_count} 个规格可用",
                ),
                self._workflow_gate(
                    "reference_assets",
                    "参考资产登记",
                    True,
                    f"{asset_count} 项已登记；未登记时沿用镜头规格生成",
                ),
            ]
        if stage_key == "video":
            return [
                self._workflow_gate(
                    "generated_shots",
                    "镜头生成",
                    shot_count > 0 and generated_count == shot_count,
                    f"{generated_count}/{shot_count} 个镜头已生成",
                ),
                self._workflow_gate(
                    "quality",
                    "质量检查",
                    shot_count > 0 and quality_count == shot_count,
                    f"{quality_count}/{shot_count} 个镜头通过质量检查",
                ),
                self._workflow_gate(
                    "review",
                    "镜头审核",
                    shot_count > 0 and approved_count == shot_count,
                    f"{approved_count}/{shot_count} 个镜头已通过",
                ),
            ]
        if stage_key == "postproduction":
            dialogue_current = self._dialogue_timeline_current(project)
            return [
                self._workflow_gate(
                    "final_master",
                    "合成成片",
                    final_exists and project.status == ProjectStatus.EXPORTED,
                    "成片已导出" if final_exists else "需要导出成片",
                ),
                self._workflow_gate(
                    "subtitles",
                    "字幕文件",
                    subtitle_exists,
                    "字幕已生成" if subtitle_exists else "需要生成字幕",
                ),
                self._workflow_gate(
                    "dialogue_timeline",
                    "对白时间线",
                    project.dialogue_timeline is None or dialogue_current,
                    "未启用对白轨" if project.dialogue_timeline is None else "时间线为当前版本",
                ),
            ]
        if stage_key == "delivery":
            compliance = self.project_compliance(project.brief.project_id)
            continuity = self.project_continuity(project.brief.project_id)
            gates = [
                self._workflow_gate(
                    "delivery_package",
                    "交付包",
                    package_exists,
                    "交付包已构建" if package_exists else "需要构建交付包",
                ),
                self._workflow_gate(
                    "package_verification",
                    "交付包校验",
                    delivery_verified,
                    "校验通过" if delivery_verified else "需要验证交付包",
                ),
                self._workflow_gate(
                    "compliance",
                    "合规与连续性",
                    bool(compliance.get("passed")) and bool(continuity.get("passed")),
                    "门禁通过" if compliance.get("passed") and continuity.get("passed") else "需要解决合规或连续性问题",
                ),
            ]
            if self.release_content_credentials_required():
                final_media = self.project_content_credentials(
                    project.brief.project_id
                )["summary"]["final_media"]
                gates.append(
                    self._workflow_gate(
                        "final_media_content_credential",
                        "最终成片内容凭证",
                        bool(final_media.get("ready")),
                        "当前成片已签名并独立验证"
                        if final_media.get("ready")
                        else "需要为当前最终 MP4 创建并独立验证 C2PA 凭证",
                    )
                )
            return gates
        raise WorkflowError(f"unknown production stage: {stage_key}")

    def production_workflow(self, project_id: str) -> dict[str, Any]:
        """Expose the production-stage control plane derived from real project evidence."""
        project = self._project(project_id)
        stages: list[dict[str, Any]] = []
        valid_locks: set[str] = set()
        for index, definition in enumerate(PRODUCTION_STAGES):
            fingerprint_value = self._stage_fingerprint(project, definition.key)
            lock = copy.deepcopy(project.stage_locks.get(definition.key))
            lock_is_current = bool(
                lock
                and not lock.get("invalidated_at")
                and lock.get("fingerprint") == fingerprint_value
            )
            predecessor_locked = index == 0 or PRODUCTION_STAGES[index - 1].key in valid_locks
            gates = self._workflow_stage_gates(project, definition.key)
            gates_passed = all(gate["passed"] for gate in gates)
            changed_since_lock = bool(lock and lock.get("fingerprint") != fingerprint_value)
            invalidated = bool(lock and lock.get("invalidated_at"))
            if lock_is_current:
                status = "LOCKED"
                valid_locks.add(definition.key)
            elif invalidated or changed_since_lock:
                status = "REWORK"
            elif not predecessor_locked:
                status = "WAITING"
            elif gates_passed:
                status = "READY"
            elif any(gate["passed"] for gate in gates):
                status = "IN_PROGRESS"
            else:
                status = "PENDING"
            stages.append(
                {
                    "key": definition.key,
                    "label": definition.label,
                    "description": definition.description,
                    "workspace_tab": definition.workspace_tab,
                    "index": index + 1,
                    "status": status,
                    "gates": gates,
                    "gates_passed": gates_passed,
                    "predecessor_locked": predecessor_locked,
                    "fingerprint": fingerprint_value,
                    "lock": lock,
                    "can_lock": status in {"READY", "REWORK"} and not project.archived_at,
                    "can_return": not project.archived_at and (
                        lock_is_current or any(project.stage_locks.get(item.key) for item in downstream_stages(definition.key))
                    ),
                }
            )
        latest_rework = project.stage_invalidations[-1] if project.stage_invalidations else None
        return {
            "schema_version": "mediaforge-production-workflow-v1",
            "project_id": project_id,
            "locking_required": self.stage_locking_required(),
            "locked_count": len(valid_locks),
            "stage_count": len(PRODUCTION_STAGES),
            "stages": stages,
            "latest_rework": copy.deepcopy(latest_rework),
        }

    def _invalidate_workflow_from(
        self,
        project: ProjectRuntime,
        stage_key: str,
        *,
        reason: str,
        actor: str,
        force_record: bool = False,
    ) -> list[str]:
        try:
            affected_definitions = downstream_stages(stage_key)
        except ValueError as exc:
            raise WorkflowError(str(exc)) from exc
        invalidated: list[str] = []
        timestamp = datetime.now(timezone.utc).isoformat()
        for definition in affected_definitions:
            lock = project.stage_locks.get(definition.key)
            if lock and not lock.get("invalidated_at"):
                lock["invalidated_at"] = timestamp
                lock["invalidated_reason"] = reason
                lock["invalidated_by"] = actor
                invalidated.append(definition.key)
        if invalidated or force_record:
            record = {
                "rework_id": f"rework_{uuid4().hex[:12]}",
                "from_stage": stage_key,
                "affected_stages": [item.key for item in affected_definitions],
                "invalidated_stages": invalidated,
                "reason": reason,
                "actor": actor,
                "occurred_at": timestamp,
            }
            project.stage_invalidations.append(record)
            project.stage_invalidations[:] = project.stage_invalidations[-100:]
            self._record_event(
                project,
                action="workflow.stage_invalidated",
                actor=actor,
                message=f"Production workflow returned from {stage_key}.",
                details=record,
            )
        return invalidated

    def lock_production_stage(
        self,
        project_id: str,
        stage_key: str,
        *,
        actor: str = "studio-editor",
    ) -> dict[str, Any]:
        project = self._project(project_id)
        self._ensure_active(project)
        try:
            stage_definition(stage_key)
        except ValueError as exc:
            raise WorkflowError(str(exc)) from exc
        workflow = self.production_workflow(project_id)
        stage = next(item for item in workflow["stages"] if item["key"] == stage_key)
        if not stage["can_lock"]:
            failed_gates = ", ".join(
                gate["label"] for gate in stage["gates"] if not gate["passed"]
            )
            reason = failed_gates or "需要先锁定前序阶段"
            raise WorkflowError(f"cannot lock {stage_key}: {reason}")
        project.stage_locks[stage_key] = {
            "lock_id": f"stage_lock_{uuid4().hex[:12]}",
            "stage": stage_key,
            "fingerprint": stage["fingerprint"],
            "actor": actor,
            "locked_at": datetime.now(timezone.utc).isoformat(),
            "gates": copy.deepcopy(stage["gates"]),
        }
        self._record_event(
            project,
            action="workflow.stage_locked",
            actor=actor,
            message=f"Production stage {stage_key} locked.",
            details={"stage": stage_key, "fingerprint": stage["fingerprint"]},
        )
        self._persist()
        return self.production_workflow(project_id)

    def return_production_stage(
        self,
        project_id: str,
        stage_key: str,
        *,
        reason: str,
        actor: str = "studio-editor",
    ) -> dict[str, Any]:
        project = self._project(project_id)
        self._ensure_active(project)
        try:
            stage_definition(stage_key)
        except ValueError as exc:
            raise WorkflowError(str(exc)) from exc
        clean_reason = reason.strip() or "Stage rework requested"
        invalidated = self._invalidate_workflow_from(
            project,
            stage_key,
            reason=clean_reason,
            actor=actor,
            force_record=True,
        )
        self._persist()
        return {
            "workflow": self.production_workflow(project_id),
            "impact": {
                "from_stage": stage_key,
                "affected_stages": [item.key for item in downstream_stages(stage_key)],
                "invalidated_stages": invalidated,
                "reason": clean_reason,
            },
        }

    def _require_production_stage_lock(self, project: ProjectRuntime, stage_key: str) -> None:
        if not self.stage_locking_required():
            return
        lock = project.stage_locks.get(stage_key)
        if not lock or lock.get("invalidated_at") or lock.get("fingerprint") != self._stage_fingerprint(project, stage_key):
            raise WorkflowError(f"production stage must be locked before this action: {stage_key}")

    def policy_report(self, project_id: str) -> dict[str, Any]:
        project = self._project(project_id)
        reports = project.policy_reports
        latest = reports[-1] if reports else None
        return {
            "project_id": project_id,
            "policy_version": self.policy.policy_version,
            "passed": all(report.get("passed", True) for report in reports) if reports else True,
            "count": len(reports),
            "blocked_count": sum(1 for report in reports if report.get("blocked")),
            "warning_count": sum(int(report.get("warnings") or 0) for report in reports),
            "latest": latest,
            "reports": reports,
        }

    def audit_log(self, project_id: str) -> dict[str, Any]:
        project = self._project(project_id)
        return {
            "project_id": project_id,
            "count": len(project.audit_events),
            "events": [
                self._audit_event_view(event)
                for event in project.audit_events
            ],
            "integrity": self.audit_integrity_report(project_id),
            "anchors": self.audit_anchor_report(project_id),
        }

    def audit_integrity_report(self, project_id: str) -> dict[str, Any]:
        project = self._project(project_id)
        report = verify_event_chain(
            project_id,
            [self._audit_event_view(event) for event in project.audit_events],
        )
        report["chain_origin"] = project.audit_chain_origin
        return report

    def provider_status_view(self) -> dict[str, Any]:
        payload = copy.deepcopy(self.provider_status)
        if len(self.provider_statuses) > 1:
            payload["providers"] = copy.deepcopy(self.provider_statuses)
        return payload

    def _provider_health_for(
        self,
        provider: GenerationProvider,
        provider_status: dict[str, Any],
    ) -> dict[str, Any]:
        checked_at = datetime.now(timezone.utc).isoformat()
        configured = bool(provider_status.get("configured", False))
        base = {
            "provider": provider.name,
            "configured": configured,
            "reachable": False if not configured else None,
            "healthy": False if not configured else None,
            "message": provider_status.get(
                "message",
                "Provider status is unavailable.",
            ),
            "latency_ms": None,
            "checked_at": checked_at,
            "details": provider_status.get("details", {}),
        }
        if not configured:
            return base

        checker = getattr(provider, "health_check", None)
        if not callable(checker):
            base.update(
                {
                    "healthy": True,
                    "message": (
                        "Provider is configured; no active health probe is available."
                    ),
                }
            )
            return base

        try:
            result = checker()
        except Exception as exc:  # pragma: no cover - defensive health boundary
            base.update(
                {
                    "reachable": False,
                    "healthy": False,
                    "message": f"Provider health check failed: {exc}",
                }
            )
            return base

        reachable = bool(result.get("reachable"))
        healthy = bool(result.get("healthy", reachable))
        base.update(
            {
                "reachable": reachable,
                "healthy": healthy,
                "message": result.get("message") or base["message"],
                "latency_ms": result.get("latency_ms"),
                "details": {
                    **base["details"],
                    **(result.get("details") or {}),
                },
            }
        )
        return base

    def provider_health(self) -> dict[str, Any]:
        primary = self._provider_health_for(self.provider, self.provider_status)
        registrations = self.router.registrations
        if len(registrations) <= 1:
            return primary

        status_by_name = {
            str(item.get("provider")): item
            for item in self.provider_statuses
        }
        providers = [
            self._provider_health_for(
                registration.provider,
                status_by_name.get(
                    registration.provider.name,
                    self.provider_status,
                ),
            )
            for registration in registrations
        ]
        primary["providers"] = providers
        primary["configured_provider_count"] = sum(
            1 for item in providers if item["configured"]
        )
        primary["healthy_provider_count"] = sum(
            1 for item in providers if item["healthy"]
        )
        return primary

    def provider_warmup(self) -> dict[str, Any]:
        """Run explicitly configured model warmups; never runs during read-only health checks."""
        results = []
        status_by_name = {
            str(item.get("provider")): item for item in self.provider_statuses
        }
        for registration in self.router.registrations:
            provider = registration.provider
            status = status_by_name.get(provider.name, self.provider_status)
            row = {"provider": provider.name, "configured": bool(status.get("configured")), "ready": False}
            if not row["configured"]:
                row["message"] = status.get("message") or "Provider is not configured."
                results.append(row)
                continue
            warmup = getattr(provider, "warmup", None)
            if not callable(warmup):
                row.update({"ready": True, "message": "Provider has no explicit warmup hook."})
                results.append(row)
                continue
            try:
                result = warmup() or {}
                row.update({"ready": bool(result.get("ready", result.get("reachable", False))), "message": result.get("message"), "latency_ms": result.get("latency_ms"), "details": result.get("details", {})})
            except Exception as exc:  # pragma: no cover - provider boundary
                row.update({"ready": False, "message": f"Provider warmup failed: {exc}"})
            results.append(row)
        ready = all(item["ready"] for item in results) if results else False
        return {"ready": ready, "providers": results, "completed_at": datetime.now(timezone.utc).isoformat()}

    def provider_diagnostics(self) -> dict[str, Any]:
        status = self.provider_status_view()
        health = self.provider_health()
        mode = str(status.get("mode") or "unknown")
        configured = bool(status.get("configured"))
        healthy = bool(health.get("healthy"))
        capabilities = list(status.get("capabilities") or [])
        details = dict(status.get("details") or {})
        provider_status_rows = status.get("providers") or [status]
        production_mode = any(
            item.get("mode") not in {"mock", "unknown", ""}
            for item in provider_status_rows
        )

        checks = [
            {
                "code": "configuration",
                "passed": configured,
                "blocking": True,
                "message": (
                    "Provider configuration is complete."
                    if configured
                    else status.get("message") or "Provider configuration is incomplete."
                ),
            },
            {
                "code": "connectivity",
                "passed": healthy,
                "blocking": True,
                "message": health.get("message") or "Provider connectivity is unknown.",
            },
            {
                "code": "capabilities",
                "passed": bool(capabilities),
                "blocking": True,
                "message": (
                    f"{len(capabilities)} generation capabilities are available."
                    if capabilities
                    else "No generation capability is available."
                ),
            },
        ]
        if mode == "comfyui":
            workflow_loaded = bool(details.get("workflow_loaded"))
            checks.append(
                {
                    "code": "workflow",
                    "passed": workflow_loaded,
                    "blocking": True,
                    "message": (
                        "ComfyUI workflow JSON is loaded."
                        if workflow_loaded
                        else "ComfyUI workflow JSON is not loaded."
                    ),
                }
            )
        if mode == "replicate":
            credential_configured = bool(details.get("credential_configured"))
            model_version_configured = bool(details.get("model_version_configured"))
            checks.extend(
                [
                    {
                        "code": "credentials",
                        "passed": credential_configured,
                        "blocking": True,
                        "message": (
                            "Replicate API credential is configured."
                            if credential_configured
                            else "Replicate API credential is missing."
                        ),
                    },
                    {
                        "code": "model_version",
                        "passed": model_version_configured,
                        "blocking": True,
                        "message": (
                            "Replicate model version is pinned."
                            if model_version_configured
                            else "Replicate model version is missing."
                        ),
                    },
                ]
            )

        checks.append(
            {
                "code": "production_mode",
                "passed": production_mode,
                "blocking": False,
                "message": (
                    "A real generation Provider is selected."
                    if production_mode
                    else "Mock mode is suitable for workflow validation only."
                ),
            }
        )

        provider_rows = health.get("providers") or []
        if provider_rows:
            configured_count = sum(1 for item in provider_rows if item["configured"])
            healthy_count = sum(1 for item in provider_rows if item["healthy"])
            checks.append(
                {
                    "code": "provider_pool",
                    "passed": healthy_count > 0,
                    "blocking": True,
                    "message": (
                        f"{healthy_count} of {len(provider_rows)} configured Providers are healthy."
                        if healthy_count
                        else "No configured Provider in the routing pool is healthy."
                    ),
                    "details": {
                        "configured_count": configured_count,
                        "healthy_count": healthy_count,
                    },
                }
            )

        ready_for_generation = configured and healthy and bool(capabilities)
        production_ready = ready_for_generation and production_mode
        if not configured:
            grade = "BLOCKED"
        elif not healthy:
            grade = "DEGRADED"
        elif not production_mode:
            grade = "SIMULATION"
        else:
            grade = "READY"

        next_actions: list[dict[str, str]] = []
        if not configured:
            if mode == "comfyui" and not details.get("workflow_loaded"):
                next_actions.append(
                    {
                        "code": "CONFIGURE_WORKFLOW",
                        "priority": "blocking",
                        "message": "Provide a valid COMFYUI_WORKFLOW_PATH and restart the service.",
                    }
                )
            if mode == "replicate" and not details.get("credential_configured"):
                next_actions.append(
                    {
                        "code": "CONFIGURE_CREDENTIALS",
                        "priority": "blocking",
                        "message": "Set REPLICATE_API_TOKEN and restart the service.",
                    }
                )
            if mode == "replicate" and not details.get("model_version_configured"):
                next_actions.append(
                    {
                        "code": "CONFIGURE_MODEL_VERSION",
                        "priority": "blocking",
                        "message": "Set REPLICATE_MODEL_VERSION and restart the service.",
                    }
                )
            if mode in {"local", "local-command"} and not details.get("command_configured"):
                next_actions.append(
                    {
                        "code": "CONFIGURE_LOCAL_PROVIDER",
                        "priority": "blocking",
                        "message": "Set MEDIAFORGE_LOCAL_PROVIDER_COMMAND and restart the service.",
                    }
                )
            if not next_actions:
                next_actions.append(
                    {
                        "code": "CONFIGURE_PROVIDER",
                        "priority": "blocking",
                        "message": "Configure a supported generation Provider and restart the service.",
                    }
                )
        elif not healthy:
            next_actions.append(
                {
                    "code": "CHECK_CONNECTIVITY",
                    "priority": "blocking",
                    "message": "Verify the Provider endpoint, network, and service availability.",
                }
            )
        if ready_for_generation:
            next_actions.append(
                {
                    "code": "START_GENERATION",
                    "priority": "ready",
                    "message": "The Provider can accept generation jobs.",
                }
            )
        if not production_mode:
            next_actions.append(
                {
                    "code": "SWITCH_REAL_PROVIDER",
                    "priority": "recommended",
                    "message": "Switch to ComfyUI or Replicate before production delivery.",
                }
            )

        return {
            "grade": grade,
            "ready_for_generation": ready_for_generation,
            "production_ready": production_ready,
            "status": status,
            "health": health,
            "providers": [
                {
                    "status": item,
                    "health": next(
                        (
                            row
                            for row in provider_rows
                            if row["provider"] == item.get("provider")
                        ),
                        None,
                    ),
                }
                for item in self.provider_statuses
            ],
            "checks": checks,
            "next_actions": next_actions,
            "checked_at": health["checked_at"],
        }

    def provider_contract_report(self, project_id: str | None = None) -> dict[str, Any]:
        """Validate the provider protocol without spending tokens or submitting jobs."""
        specs: list[GenerationSpec] = []
        if project_id is not None:
            project = self._project(project_id)
            specs = [runtime.spec for runtime in project.shots.values()]
        status_by_name = {
            str(row.get("provider")): row
            for row in (self.provider_statuses or [self.provider_status])
        }
        provider_rows = []
        for registration in self.router.registrations:
            provider = registration.provider
            protocol_checks = {
                "name": isinstance(getattr(provider, "name", None), str) and bool(provider.name),
                "supports": callable(getattr(provider, "supports", None)),
                "estimate_cost": callable(getattr(provider, "estimate_cost", None)),
                "generate": callable(getattr(provider, "generate", None)),
            }
            capabilities = []
            errors: list[str] = []
            for capability in Capability:
                supported: bool | None = None
                try:
                    supported = bool(provider.supports(capability))
                except Exception as exc:  # pragma: no cover - external provider boundary
                    errors.append(f"supports({capability}): {exc}")
                capabilities.append({"capability": capability, "supported": supported})

            estimate_checks = []
            for spec in specs:
                try:
                    supports_spec = bool(provider.supports(spec.provider_constraints.capability))
                except Exception as exc:  # pragma: no cover - external provider boundary
                    errors.append(f"{spec.shot_id}: supports: {exc}")
                    estimate_checks.append({"shot_id": spec.shot_id, "estimated_cost": None, "passed": False})
                    continue
                if not supports_spec:
                    continue
                try:
                    estimated = float(provider.estimate_cost(spec))
                    valid = math.isfinite(estimated) and estimated >= 0
                    estimate_checks.append(
                        {
                            "shot_id": spec.shot_id,
                            "estimated_cost": round(estimated, 6) if valid else None,
                            "passed": valid,
                        }
                    )
                    if not valid:
                        errors.append(f"{spec.shot_id}: estimate_cost returned an invalid value")
                except Exception as exc:  # pragma: no cover - external provider boundary
                    estimate_checks.append({"shot_id": spec.shot_id, "estimated_cost": None, "passed": False})
                    errors.append(f"{spec.shot_id}: estimate_cost: {exc}")
            provider_rows.append(
                {
                    "provider": provider.name,
                    "enabled": registration.enabled,
                    "priority": registration.priority,
                    "configured": bool(status_by_name.get(provider.name, {}).get("configured", False)),
                    "protocol_checks": protocol_checks,
                    "capabilities": capabilities,
                    "estimate_checks": estimate_checks,
                    "passed": all(protocol_checks.values()) and not errors,
                    "errors": errors,
                }
            )

        routing = []
        for spec in specs:
            try:
                decision = self.router.select(spec)
                routing.append(
                    {
                        "shot_id": spec.shot_id,
                        "capability": spec.provider_constraints.capability,
                        "routable": True,
                        "provider": decision.provider.name,
                        "estimated_cost": decision.estimated_cost,
                    }
                )
            except (ProviderRoutingError, ValueError) as exc:
                routing.append(
                    {
                        "shot_id": spec.shot_id,
                        "capability": spec.provider_constraints.capability,
                        "routable": False,
                        "error": str(exc),
                    }
                )
        enabled_rows = [row for row in provider_rows if row["enabled"]]
        return {
            "schema_version": "mediaforge-provider-contract-v1",
            "project_id": project_id,
            "checked_at": datetime.now(timezone.utc).isoformat(),
            "provider_count": len(provider_rows),
            "providers": provider_rows,
            "routing": routing,
            "summary": {
                "protocol_passed": all(row["passed"] for row in enabled_rows) if enabled_rows else False,
                "routable_shot_count": sum(1 for row in routing if row["routable"]),
                "unroutable_shot_count": sum(1 for row in routing if not row["routable"]),
                "planned_shot_count": len(specs),
            },
        }

    def validate_provider_contracts(
        self,
        project_id: str,
        *,
        actor: str = "studio-operations",
    ) -> dict[str, Any]:
        project = self._project(project_id)
        report = self.provider_contract_report(project_id)
        self._record_event(
            project,
            action="providers.contract_validated",
            actor=actor,
            message="Provider protocol contract validated without generation.",
            details=copy.deepcopy(report["summary"]),
        )
        self._persist()
        return report

    def shot_view(self, runtime: ShotRuntime) -> dict[str, Any]:
        job = None
        if runtime.current_job_id:
            try:
                job = self._job_view(self.jobs.get(runtime.current_job_id))
            except KeyError:
                job = None
        return {
            "shot": runtime.shot.model_dump(mode="json"),
            "spec": runtime.spec.model_dump(mode="json"),
            "review_status": runtime.review_status,
            "revision": runtime.revision,
            "current_job_id": runtime.current_job_id,
            "job": job,
            "artifact": runtime.current_artifact,
            "route": runtime.route,
            "quality": runtime.quality,
            "artifact_history": runtime.artifact_history,
            "variants": runtime.variants,
            "reviews": [
                {
                    "status": review.status,
                    "comment": review.comment,
                    "actor": review.actor,
                    "occurred_at": review.occurred_at.isoformat(),
                }
                for review in runtime.reviews
            ],
        }

    @staticmethod
    def _audit_event_view(event: AuditEvent) -> dict[str, Any]:
        return {
            "action": event.action,
            "actor": event.actor,
            "message": event.message,
            "shot_id": event.shot_id,
            "details": event.details,
            "trace_id": event.trace_id,
            "occurred_at": event.occurred_at.isoformat(),
            "sequence": event.sequence,
            "previous_hash": event.previous_hash,
            "event_hash": event.event_hash,
        }

    @staticmethod
    def _job_view(job: JobRecord) -> dict[str, Any]:
        first_event = job.events[0].occurred_at if job.events else None
        last_event = job.events[-1].occurred_at if job.events else None
        return {
            **job.as_dict(),
            "trace_id": job.trace_id,
            "created_at": first_event.isoformat() if first_event else None,
            "updated_at": last_event.isoformat() if last_event else None,
            "artifact_count": len(job.artifacts),
        }

    @staticmethod
    def _probe_artifact_media(artifact: Any) -> Any:
        path = Path(artifact.uri)
        return probe_image(path) if artifact.kind == "image" else probe_video(path)

    @staticmethod
    def _artifact_metadata_issue(asset: dict[str, Any]) -> str | None:
        metadata_uri = str(asset.get("metadata_uri") or "").strip()
        if not metadata_uri:
            return "metadata_uri is missing"
        metadata_path = Path(metadata_uri)
        if not metadata_path.is_file():
            return "metadata sidecar is missing"
        try:
            payload = json.loads(metadata_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            return "metadata sidecar is not valid JSON"
        if not isinstance(payload, dict):
            return "metadata sidecar must contain a JSON object"
        if payload.get("schema_version") != "mediaforge-artifact-metadata-v1":
            return "metadata schema version is unsupported"
        if not str(payload.get("provider") or "").strip():
            return "metadata provider is missing"
        if not str(payload.get("job_id") or "").strip():
            return "metadata job_id is missing"
        return None

    @staticmethod
    def _artifact_for_export(
        artifact: dict[str, Any],
        runtime: ShotRuntime,
        output_dir: Path,
    ) -> Path:
        path = Path(artifact["uri"])
        if artifact["kind"] != "image":
            return path
        normalized_dir = output_dir / "normalized"
        normalized_path = normalized_dir / f"{artifact['artifact_id']}.mp4"
        if normalized_path.exists():
            return normalized_path
        duration = float(
            artifact.get("duration_seconds")
            or runtime.spec.intent.duration_seconds
            or 1.0
        )
        return create_video_from_image(
            path,
            normalized_path,
            duration_seconds=duration,
        )

    def _evaluate_artifact_quality(
        self,
        artifact: Any,
        probe_result: Any,
        spec: GenerationSpec,
    ) -> dict[str, Any]:
        quality = self._quality_report(
            probe_result,
            spec,
            media_kind=artifact.kind,
        )
        blocking = os.getenv("MEDIAFORGE_VISUAL_GATE", "false").lower() in {"true", "1"}
        quality["visual_evaluation"] = visual_report(Path(artifact.uri), artifact.kind, blocking=blocking)
        quality["evaluated_at"] = datetime.now(timezone.utc).isoformat()
        actual_hash = sha256_file(Path(artifact.uri)) if Path(artifact.uri).is_file() else None
        quality["artifact_sha256"] = actual_hash
        if actual_hash != artifact.sha256:
            quality["checks"].append({"name": "artifact_integrity", "passed": False, "observed": actual_hash, "expected": artifact.sha256})
            quality["passed"] = False
        if blocking:
            quality["checks"].extend(quality["visual_evaluation"]["checks"])
            quality["passed"] = bool(quality["passed"] and quality["visual_evaluation"]["passed"])
        external = self.quality_evaluator.evaluate(
            Path(artifact.uri),
            artifact_id=artifact.artifact_id,
            media_kind=artifact.kind,
            local_quality=quality,
            spec=spec,
            reference_root=self.output_root / spec.project_id,
        )
        quality["external_evaluation"] = external.as_dict()
        if external.passed is not None:
            quality["checks"].extend(external.checks)
            quality["checks"].append({"name": "external_decision", "passed": external.passed, "observed": external.score, "expected": "external evaluator approval"})
            quality["passed"] = bool(quality["passed"] and external.passed)
            quality["quality_score"] = round(
                (
                    sum(1 for check in quality["checks"] if check.get("passed"))
                    / len(quality["checks"])
                )
                if quality["checks"]
                else 0.0,
                4,
            )
        elif external.error and not self.quality_evaluator.fail_open:
            quality["passed"] = False
        quality["quality_score"] = round(sum(bool(check.get("passed")) for check in quality["checks"]) / len(quality["checks"]), 4)
        return quality

    @staticmethod
    def _quality_report(
        probe_result: Any,
        spec: GenerationSpec,
        *,
        media_kind: str = "video",
    ) -> dict[str, Any]:
        expected_duration = float(spec.intent.duration_seconds)
        duration = probe_result.duration_seconds
        width = probe_result.width
        height = probe_result.height
        if media_kind == "image":
            checks = [
                {
                    "name": "decode",
                    "passed": bool(probe_result.valid),
                    "observed": "readable" if probe_result.valid else "unreadable",
                    "expected": "readable image file",
                },
                {
                    "name": "format",
                    "passed": bool(getattr(probe_result, "format", None)),
                    "observed": getattr(probe_result, "format", None),
                    "expected": "known image format",
                },
                {
                    "name": "resolution",
                    "passed": (
                        width is not None
                        and height is not None
                        and width >= 320
                        and height >= 180
                    ),
                    "observed": f"{width or 0}x{height or 0}",
                    "expected": ">=320x180",
                },
            ]
        else:
            checks = [
                {
                    "name": "decode",
                    "passed": bool(probe_result.valid),
                    "observed": "readable" if probe_result.valid else "unreadable",
                    "expected": "readable video file",
                },
                {
                    "name": "duration",
                    "passed": (
                        duration is not None
                        and abs(duration - expected_duration) <= 1.0
                    ),
                    "observed": duration,
                    "expected": expected_duration,
                },
                {
                    "name": "resolution",
                    "passed": (
                        width is not None
                        and height is not None
                        and width >= 320
                        and height >= 180
                    ),
                    "observed": f"{width or 0}x{height or 0}",
                    "expected": ">=320x180",
                },
            ]
        visual_signal = (
            inspect_image_visual_signal(Path(probe_result.path))
            if media_kind == "image" and probe_result.valid
            else None
        )
        return {
            "policy_version": "media-quality-v1",
            "media_kind": media_kind,
            "passed": all(check["passed"] for check in checks),
            "duration_seconds": duration,
            "width": width,
            "height": height,
            "format": getattr(probe_result, "format", None),
            "error": probe_result.error,
            "checks": checks,
            "visual_signal": visual_signal,
            "quality_score": round(
                sum(1 for check in checks if check["passed"]) / len(checks),
                4,
            ) if checks else 0.0,
        }

    @staticmethod
    def _variant_spec(runtime: ShotRuntime, variant_index: int) -> GenerationSpec:
        motions = [
            "slow_push_in",
            "locked_off",
            "handheld_drift",
            "slow_pull_back",
        ]
        strength_offsets = [-0.12, 0.0, 0.1, 0.16]
        base_strength = runtime.spec.workflow.controlnet.strength
        strength = max(
            0.1,
            min(
                1.0,
                base_strength
                + strength_offsets[(variant_index - 1) % len(strength_offsets)],
            ),
        )
        return runtime.spec.model_copy(
            update={
                "asset_versions": {
                    **runtime.spec.asset_versions,
                    "ab_variant": f"v{variant_index}",
                },
                "intent": runtime.spec.intent.model_copy(
                    update={
                        "camera_motion": motions[
                            (variant_index - 1) % len(motions)
                        ],
                    }
                ),
                "workflow": runtime.spec.workflow.model_copy(
                    update={
                        "controlnet": runtime.spec.workflow.controlnet.model_copy(
                            update={"strength": round(strength, 2)}
                        ),
                    }
                ),
            }
        )

    @staticmethod
    def _score_variant(
        quality: dict[str, Any] | None,
        route: dict[str, Any],
        spec: GenerationSpec,
    ) -> dict[str, Any]:
        checks = quality.get("checks", []) if quality else []
        check_score = (
            sum(1 for check in checks if check.get("passed")) / len(checks)
            if checks
            else 0.0
        )
        estimated_cost = float(route.get("estimated_cost") or 0.0)
        max_cost = float(spec.provider_constraints.max_cost or 0.0)
        budget_score = (
            max(0.0, min(1.0, 1 - estimated_cost / max_cost))
            if max_cost
            else 0.0
        )
        quality_score = 1.0 if quality and quality.get("passed") else 0.0
        total = round(
            quality_score * 0.60
            + check_score * 0.25
            + budget_score * 0.15,
            4,
        )
        return {
            "total": total,
            "passed": bool(quality and quality.get("passed")),
            "breakdown": {
                "quality": round(quality_score, 4),
                "checks": round(check_score, 4),
                "budget": round(budget_score, 4),
            },
        }

    def _build_shot_comparison(
        self,
        project: ProjectRuntime,
        runtime: ShotRuntime,
    ) -> dict[str, Any]:
        candidates: list[dict[str, Any]] = []
        if runtime.current_artifact:
            score = self._score_variant(
                runtime.quality,
                runtime.route or {},
                runtime.spec,
            )
            candidates.append(
                {
                    "source": "current",
                    "variant_id": "current",
                    "label": "Current version",
                    "shot_id": runtime.shot.shot_id,
                    "revision": runtime.revision,
                    "job_id": runtime.current_job_id,
                    "artifact_id": runtime.current_artifact["artifact_id"],
                    "artifact": runtime.current_artifact,
                    "provider": (runtime.route or {}).get("provider"),
                    "estimated_cost": (runtime.route or {}).get("estimated_cost"),
                    "quality_passed": bool(
                        runtime.quality and runtime.quality.get("passed")
                    ),
                    "score": score,
                    "promoted": True,
                    "selected": True,
                    "created_at": runtime.current_artifact.get("created_at"),
                }
            )

        for variant in runtime.variants:
            score = variant.get("score") or {
                "total": 0.0,
                "passed": False,
                "breakdown": {},
            }
            artifact = variant.get("artifact")
            route = variant.get("route") or {}
            candidates.append(
                {
                    "source": "variant",
                    "variant_id": variant["variant_id"],
                    "label": variant.get("label", variant["variant_id"]),
                    "shot_id": runtime.shot.shot_id,
                    "revision": variant.get("revision", runtime.revision),
                    "job_id": variant.get("job_id"),
                    "artifact_id": artifact.get("artifact_id") if artifact else None,
                    "artifact": artifact,
                    "provider": route.get("provider"),
                    "estimated_cost": route.get("estimated_cost"),
                    "quality_passed": bool(
                        variant.get("quality")
                        and variant["quality"].get("passed")
                    ),
                    "score": score,
                    "promoted": bool(variant.get("promoted")),
                    "selected": bool(variant.get("selected")),
                    "created_at": variant.get("created_at"),
                    "error": variant.get("error"),
                }
            )

        ranked = sorted(
            candidates,
            key=lambda candidate: (
                -float(candidate.get("score", {}).get("total") or 0.0),
                float(candidate.get("estimated_cost") or 0.0),
                0 if candidate["source"] == "variant" else 1,
                candidate["label"],
            ),
        )
        recommended = ranked[0] if ranked else None
        return {
            "schema_version": "mediaforge-shot-comparison-v1",
            "comparison_id": f"cmp_{uuid4().hex[:12]}",
            "project_id": project.brief.project_id,
            "shot_id": runtime.shot.shot_id,
            "revision": runtime.revision,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "candidate_count": len(candidates),
            "variant_count": len(runtime.variants),
            "recommended_variant_id": (
                recommended["variant_id"] if recommended else None
            ),
            "recommended_source": recommended["source"] if recommended else None,
            "current_artifact_id": (
                runtime.current_artifact["artifact_id"]
                if runtime.current_artifact
                else None
            ),
            "ranking": ranked,
            "summary": {
                "best_score": (
                    recommended["score"]["total"] if recommended else None
                ),
                "promoted_variant_id": next(
                    (
                        variant["variant_id"]
                        for variant in runtime.variants
                        if variant.get("selected")
                    ),
                    None,
                ),
                "total_variant_cost": round(
                    sum(
                        float((variant.get("route") or {}).get("estimated_cost") or 0.0)
                        for variant in runtime.variants
                    ),
                    4,
                ),
            },
        }

    def _generate_artifact(
        self,
        provider: GenerationProvider,
        spec: GenerationSpec,
        *,
        job_id: str,
        output_dir: Path,
        estimated_cost: float,
        tenant_id: str | None = None,
        project_id: str | None = None,
    ) -> Artifact:
        started = perf_counter()
        outcome = 'failed'
        try:
            artifact = provider.generate(
                spec,
                job_id=job_id,
                output_dir=output_dir,
            )
            outcome = 'succeeded'
            self.enterprise.billing.record(
                event_id=f"generation:{job_id}:{self.jobs.get(job_id).attempts}",
                tenant_id=str(tenant_id or "default"),
                project_id=project_id,
                category="provider_generation",
                quantity=1,
                unit_price=max(float(estimated_cost), 0.0),
                metadata={"provider": provider.name, "artifact_id": artifact.artifact_id, "cost_basis": "estimate"},
            )
            return artifact
        finally:
            self.runtime_metrics.observe_provider(
                provider=provider.name,
                outcome=outcome,
                duration=perf_counter() - started,
                estimated_cost=estimated_cost,
            )

    def _record_event(
        self,
        project: ProjectRuntime,
        *,
        action: str,
        actor: str,
        message: str,
        shot_id: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        self._ensure_audit_chain_for_append(project)
        previous_event = project.audit_events[-1] if project.audit_events else None
        event = AuditEvent(
            action=action,
            actor=actor,
            message=message,
            shot_id=shot_id,
            details=copy.deepcopy(details or {}),
            trace_id=project.trace_id,
            occurred_at=datetime.now(timezone.utc),
            sequence=len(project.audit_events) + 1,
            previous_hash=previous_event.event_hash if previous_event else None,
        )
        event.event_hash = audit_event_hash(
            project.brief.project_id,
            self._audit_event_view(event),
        )
        project.audit_events.append(event)
        event_view = self._audit_event_view(event)
        self.webhooks.emit(
            project_id=project.brief.project_id,
            event=event_view,
        )
        self.siem.emit(
            project_id=project.brief.project_id,
            event=event_view,
        )

    def _ensure_audit_chain_for_append(self, project: ProjectRuntime) -> None:
        if not project.audit_events:
            return
        events = project.audit_events
        has_chain_data = [
            event.sequence is not None
            or event.previous_hash is not None
            or event.event_hash is not None
            for event in events
        ]
        if not any(has_chain_data):
            previous_hash: str | None = None
            for sequence, event in enumerate(events, start=1):
                event.sequence = sequence
                event.previous_hash = previous_hash
                event.event_hash = audit_event_hash(
                    project.brief.project_id,
                    self._audit_event_view(event),
                )
                previous_hash = event.event_hash
            project.audit_chain_origin = "legacy_resealed"
            return
        if not all(has_chain_data):
            raise WorkflowError(
                "audit chain contains mixed sealed and unsealed events; verify or restore the project before writing"
            )
        report = self.audit_integrity_report(project.brief.project_id)
        if not report["verified"]:
            raise WorkflowError(
                "audit chain verification failed; restore a verified project snapshot before writing"
            )

    def _cost_report(self, project: ProjectRuntime) -> dict[str, Any]:
        def route_cost(route: dict[str, Any]) -> float:
            attempts = route.get("attempts")
            if isinstance(attempts, list) and attempts:
                return sum(
                    float(
                        item.get("actual_cost")
                        if item.get("actual_cost") is not None
                        else item.get("estimated_cost") or 0.0
                    )
                    for item in attempts
                    if isinstance(item, dict)
                )
            return float(
                route.get("actual_cost")
                if route.get("actual_cost") is not None
                else route.get("estimated_cost") or 0.0
            )

        shot_breakdown = []
        spent = 0.0
        for runtime in project.shots.values():
            shot_spent = 0.0
            attempts = 0
            seen_job_ids: set[str] = set()
            for entry in runtime.artifact_history:
                job_id = entry.get("job_id")
                if job_id and job_id in seen_job_ids:
                    continue
                if job_id:
                    seen_job_ids.add(job_id)
                attempts += 1
                route = entry.get("route") or {}
                shot_spent += route_cost(route)
            variant_attempts = 0
            for variant in runtime.variants:
                job_id = variant.get("job_id")
                if job_id and job_id in seen_job_ids:
                    continue
                if job_id:
                    seen_job_ids.add(job_id)
                variant_attempts += 1
                attempts += 1
                route = variant.get("route") or {}
                shot_spent += route_cost(route)
            spent += shot_spent
            shot_breakdown.append(
                {
                    "shot_id": runtime.shot.shot_id,
                    "revision": runtime.revision,
                    "review_status": runtime.review_status,
                    "attempts": attempts,
                    "variant_attempts": variant_attempts,
                    "spent": round(shot_spent, 4),
                    "last_estimated_cost": (
                        round(float(runtime.route["estimated_cost"]), 4)
                        if runtime.route and runtime.route.get("estimated_cost") is not None
                        else None
                    ),
                }
            )

        budget = float(project.brief.budget)
        remaining = budget - spent
        return {
            "budget": round(budget, 4),
            "spent": round(spent, 4),
            "remaining": round(max(remaining, 0.0), 4),
            "overspend": round(max(-remaining, 0.0), 4),
            "spent_ratio": round(spent / budget, 4) if budget else None,
            "shot_breakdown": shot_breakdown,
        }

    def _queued_jobs(self, project_id: str) -> list[JobRecord]:
        return [
            job
            for job in self.jobs.all()
            if job.spec.project_id == project_id
            and job.status in {
                JobStatus.VALIDATED,
                JobStatus.QUEUED,
                JobStatus.ADMITTED,
            }
        ]

    def _retry_wait_jobs(self, project_id: str) -> list[JobRecord]:
        return [
            job
            for job in self.jobs.all()
            if job.spec.project_id == project_id
            and job.retry_at is not None
            and job.status in {
                JobStatus.FAILED,
                JobStatus.QUALITY_REJECTED,
                JobStatus.RETRY_WAIT,
            }
        ]

    def _reserved_generation_cost(self, project: ProjectRuntime) -> float:
        reserved = 0.0
        for job in self._queued_jobs(project.brief.project_id):
            try:
                decision = self.router.select(job.spec)
            except ProviderRoutingError:
                continue
            reserved += decision.estimated_cost
        for job in self._retry_wait_jobs(project.brief.project_id):
            try:
                decision = self.router.select(job.spec)
            except ProviderRoutingError:
                continue
            reserved += decision.estimated_cost
        return reserved

    def _queue_summary(self, project_id: str) -> dict[str, Any]:
        queued_jobs = self._queued_jobs(project_id)
        retry_wait_jobs = self._retry_wait_jobs(project_id)
        stale_jobs = self._stale_jobs_for_project(project_id)
        due_retries = self.jobs.due_retries()
        due_retry_ids = {
            job.job_id
            for job in due_retries
            if job.spec.project_id == project_id
        }
        next_retry_at = min(
            (
                job.retry_at
                for job in retry_wait_jobs
                if job.retry_at is not None
            ),
            default=None,
        )
        return {
            "queued_jobs": len(queued_jobs),
            "queued_shots": [job.spec.shot_id for job in queued_jobs],
            "retry_wait_jobs": len(retry_wait_jobs),
            "retry_due_jobs": len(due_retry_ids),
            "stale_jobs": len(stale_jobs),
            "stale_job_ids": [job["job_id"] for job in stale_jobs],
            "job_lease_seconds": self.job_lease_policy.stale_after_seconds,
            "next_retry_at": (
                next_retry_at.isoformat() if next_retry_at else None
            ),
            "retry_policy": {
                "max_attempts": self.retry_policy.max_attempts,
                "base_delay_seconds": self.retry_policy.base_delay_seconds,
            },
            "reserved_cost": round(
                self._reserved_generation_cost(self._project(project_id)),
                4,
            ),
            "statuses": {
                status.value: sum(1 for job in queued_jobs if job.status == status)
                for status in [
                    JobStatus.VALIDATED,
                    JobStatus.QUEUED,
                    JobStatus.ADMITTED,
                ]
            },
        }

    def _stale_jobs_for_project(self, project_id: str) -> list[dict[str, Any]]:
        now = datetime.now(timezone.utc)
        stale: list[dict[str, Any]] = []
        for job in self.jobs.all():
            if job.spec.project_id != project_id:
                continue
            if job.status not in {JobStatus.ADMITTED, JobStatus.RUNNING}:
                continue
            occurred_at = job.events[-1].occurred_at if job.events else now
            if occurred_at.tzinfo is None:
                occurred_at = occurred_at.replace(tzinfo=timezone.utc)
            age_seconds = max((now - occurred_at).total_seconds(), 0.0)
            if age_seconds < self.job_lease_policy.stale_after_seconds:
                continue
            view = self._job_view(job)
            view["lease_age_seconds"] = round(age_seconds, 2)
            stale.append(view)
        return stale

    def _project(self, project_id: str) -> ProjectRuntime:
        try:
            return self.projects[project_id]
        except KeyError as exc:
            raise ProjectNotFound(project_id) from exc

    @staticmethod
    def _ensure_active(project: ProjectRuntime) -> None:
        if project.archived_at is not None:
            raise WorkflowError("project is archived; restore it before editing")

    def _build_evaluation_report(self, project_id: str) -> dict[str, Any]:
        project = self._project(project_id)
        shots = list(project.shots.values())
        shot_count = len(shots)
        generated_count = sum(1 for runtime in shots if runtime.current_artifact)
        approved_count = sum(
            1
            for runtime in shots
            if runtime.review_status == ReviewStatus.APPROVED
        )
        quality_count = sum(
            1
            for runtime in shots
            if runtime.quality and runtime.quality.get("passed")
        )
        cost = self._cost_report(project)
        policy = self.policy_report(project_id)
        continuity = self.project_continuity(project_id)
        inventory = self.asset_inventory(project_id)
        final_probe = (
            probe_video(Path(project.final_mp4))
            if project.final_mp4
            else None
        )
        package_ready = (
            bool(project.delivery_package)
            and Path(project.delivery_package).is_file()
        )

        gates: list[dict[str, Any]] = []

        def add_gate(
            name: str,
            label: str,
            passed: bool,
            weight: int,
            observed: Any,
            expected: Any,
        ) -> None:
            gates.append(
                {
                    "name": name,
                    "label": label,
                    "passed": bool(passed),
                    "weight": weight,
                    "observed": observed,
                    "expected": expected,
                }
            )

        add_gate(
            "plan_complete",
            "Story plan exists",
            shot_count > 0,
            5,
            shot_count,
            ">= 1 shot",
        )
        add_gate(
            "generation_complete",
            "All shots generated",
            shot_count > 0 and generated_count == shot_count,
            15,
            f"{generated_count}/{shot_count}",
            f"{shot_count}/{shot_count}",
        )
        add_gate(
            "approvals_complete",
            "All shots approved",
            shot_count > 0 and approved_count == shot_count,
            15,
            f"{approved_count}/{shot_count}",
            f"{shot_count}/{shot_count}",
        )
        add_gate(
            "media_quality",
            "Generated media passes quality",
            shot_count > 0 and quality_count == shot_count,
            15,
            f"{quality_count}/{shot_count}",
            f"{shot_count}/{shot_count}",
        )
        add_gate(
            "policy_clear",
            "Policy gate is clear",
            policy["passed"],
            10,
            {
                "blocked": policy["blocked_count"],
                "warnings": policy["warning_count"],
            },
            "0 blocking findings",
        )
        add_gate(
            "continuity_clear",
            "Story and timeline continuity passes",
            continuity["passed"],
            5,
            continuity["summary"],
            "all continuity checks passed",
        )
        add_gate(
            "budget_guard",
            "Spend stays within budget",
            cost["spent"] <= cost["budget"],
            10,
            f"${cost['spent']:.2f} / ${cost['budget']:.2f}",
            "spent <= budget",
        )
        add_gate(
            "final_exported",
            "Final sample is exported",
            bool(final_probe and final_probe.valid),
            10,
            (
                {
                    "path": final_probe.path,
                    "duration_seconds": final_probe.duration_seconds,
                    "width": final_probe.width,
                    "height": final_probe.height,
                }
                if final_probe
                else None
            ),
            "readable final MP4",
        )
        add_gate(
            "delivery_packaged",
            "Delivery package exists",
            package_ready,
            5,
            project.delivery_package,
            "delivery ZIP on disk",
        )
        add_gate(
            "release_recorded",
            "Release record exists",
            project.release is not None,
            5,
            project.release["release_id"] if project.release else None,
            "release_id",
        )
        add_gate(
            "asset_provenance",
            "Asset provenance is complete",
            (
                inventory["summary"]["reference_assets"] >= 2
                and inventory["summary"]["current_media_assets"] == shot_count
                if shot_count
                else False
            ),
            5,
            inventory["summary"],
            "references and current generated media for all shots",
        )

        total_weight = sum(gate["weight"] for gate in gates)
        earned = sum(gate["weight"] for gate in gates if gate["passed"])
        score = round(earned / total_weight, 4) if total_weight else 0.0
        model_gates = self._model_evaluation_gates({"gates": gates})
        model_weight = sum(gate["weight"] for gate in model_gates)
        model_earned = sum(gate["weight"] for gate in model_gates if gate["passed"])
        model_quality_score = round(model_earned / model_weight, 4) if model_weight else 0.0
        failed = [gate for gate in gates if not gate["passed"]]
        recommendations = [
            f"{gate['label']}: expected {gate['expected']}, observed {gate['observed']}"
            for gate in failed
        ]
        return {
            "project_id": project_id,
            "policy_version": "delivery-evaluation-v1",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "passed": not failed,
            "score": score,
            "score_percent": int(round(score * 100)),
            "model_quality_score": model_quality_score,
            "model_quality_score_percent": int(round(model_quality_score * 100)),
            "gate_count": len(gates),
            "failed_gate_count": len(failed),
            "gates": gates,
            "recommendations": recommendations,
            "summary": {
                "shot_count": shot_count,
                "generated_shots": generated_count,
                "approved_shots": approved_count,
                "quality_passed_shots": quality_count,
                "released": project.release is not None,
                "packaged": package_ready,
                "cost": cost,
                "policy": {
                    "passed": policy["passed"],
                    "warning_count": policy["warning_count"],
                    "blocked_count": policy["blocked_count"],
                },
                "continuity": {
                    "passed": continuity["passed"],
                    "shot_count": continuity["summary"]["shot_count"],
                    "duration_seconds": continuity["summary"]["duration_seconds"],
                },
            },
        }

    def _build_provider_benchmark(self, project_id: str) -> dict[str, Any]:
        project = self._project(project_id)
        specs = [runtime.spec for runtime in project.shots.values()]
        provider_rows = []
        for registration in self.router.registrations:
            started = perf_counter()
            costs = []
            supported = 0
            within_budget = 0
            errors = []
            for spec in specs:
                try:
                    if registration.provider.supports(
                        spec.provider_constraints.capability
                    ):
                        supported += 1
                        estimated_cost = registration.provider.estimate_cost(spec)
                        costs.append(estimated_cost)
                        if estimated_cost <= spec.provider_constraints.max_cost:
                            within_budget += 1
                    else:
                        errors.append(
                            f"{spec.shot_id}: unsupported "
                            f"{spec.provider_constraints.capability}"
                        )
                except Exception as exc:  # pragma: no cover - defensive status capture
                    errors.append(f"{spec.shot_id}: {exc}")
            elapsed_ms = (perf_counter() - started) * 1000
            provider_rows.append(
                {
                    "name": registration.provider.name,
                    "enabled": registration.enabled,
                    "priority": registration.priority,
                    "supported_shots": supported,
                    "within_budget_shots": within_budget,
                    "estimated_total_cost": round(sum(costs), 4),
                    "estimated_average_cost": (
                        round(sum(costs) / len(costs), 4)
                        if costs
                        else None
                    ),
                    "average_estimate_latency_ms": round(
                        elapsed_ms / max(len(specs), 1),
                        4,
                    ),
                    "errors": errors,
                }
            )

        route_counts: dict[str, int] = {}
        selected_cost = 0.0
        route_errors = []
        for spec in specs:
            try:
                decision = self.router.select(spec)
                route_counts[decision.provider.name] = (
                    route_counts.get(decision.provider.name, 0) + 1
                )
                selected_cost += decision.estimated_cost
            except ProviderRoutingError as exc:
                route_errors.append(f"{spec.shot_id}: {exc}")

        eligible = [
            provider
            for provider in provider_rows
            if provider["enabled"]
            and provider["supported_shots"] == len(specs)
            and provider["within_budget_shots"] == len(specs)
        ]
        eligible.sort(
            key=lambda provider: (
                provider["estimated_total_cost"],
                -provider["priority"],
                provider["name"],
            )
        )
        return {
            "project_id": project_id,
            "policy_version": "provider-benchmark-v1",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "shot_count": len(specs),
            "provider_count": len(provider_rows),
            "providers": provider_rows,
            "selected_routes": {
                "counts": route_counts,
                "estimated_total_cost": round(selected_cost, 4),
                "errors": route_errors,
            },
            "recommended_provider": eligible[0]["name"] if eligible else None,
            "provider_status": self.provider_status,
        }

    @staticmethod
    def _clone_story_bible(
        story_bible: dict[str, Any] | None,
        brief: CreativeBrief,
    ) -> dict[str, Any] | None:
        if not story_bible:
            return None
        cloned = copy.deepcopy(story_bible)
        cloned["project_id"] = brief.project_id
        cloned["title"] = brief.title
        cloned["premise"] = brief.premise
        return cloned

    @staticmethod
    def _runtime_template(
        runtime: ShotRuntime,
        project_id: str,
    ) -> ShotRuntime:
        shot = ShotCard.model_validate(
            {
                **runtime.shot.model_dump(mode="json"),
                "project_id": project_id,
            }
        )
        spec = GenerationSpec.model_validate(
            {
                **runtime.spec.model_dump(mode="json"),
                "project_id": project_id,
            }
        )
        return ShotRuntime(shot=shot, spec=spec)

    def _policy_reports_for_template(
        self,
        project: ProjectRuntime,
    ) -> list[dict[str, Any]]:
        reports = [self.policy.assess_brief(project.brief).as_dict()]
        reports.extend(
            self.policy.assess_shot(runtime.shot, runtime.spec).as_dict()
            for runtime in project.shots.values()
        )
        return reports

    def _write_governance_files(self, project_id: str) -> list[Path]:
        project = self._project(project_id)
        output_dir = self.output_root / project_id
        output_dir.mkdir(parents=True, exist_ok=True)
        documents: dict[str, dict[str, Any]] = {
            "asset-inventory.json": self.asset_inventory(project_id),
            "audit-log.json": self.audit_log(project_id),
            "audit-integrity.json": self.audit_integrity_report(project_id),
            "audit-anchors.json": self.audit_anchor_report(project_id),
            "content-credentials.json": self.project_content_credentials(project_id),
            "compliance-report.json": self.project_compliance(project_id),
            "continuity-report.json": self.project_continuity(project_id),
            "distribution-report.json": self.distribution_report(project_id),
            "evaluation-report.json": self.latest_evaluation_report(project_id),
            "llmops-report.json": self.llmops_summary(project_id),
            "policy-report.json": self.policy_report(project_id),
            "production-report.json": self.production_report(project_id),
            "provenance-report.json": self.project_provenance(project_id),
            "provider-benchmark.json": self.latest_provider_benchmark(project_id),
            "provider-contract.json": self.provider_contract_report(project_id),
            "retrospective-report.json": self.retrospective_report(project_id),
            "route-preview.json": self.project_routes(project_id),
            "shot-comparisons.json": self.project_comparisons(project_id),
            "trace-report.json": self.project_trace(project_id),
        }
        acceptance_report = self._acceptance_report(project_id)
        if project.archived_at is not None and acceptance_report.is_file():
            documents["acceptance-report.json"] = json.loads(
                acceptance_report.read_text(encoding="utf-8")
            )
        closeout_report = self._closeout_report(project_id)
        if closeout_report.is_file():
            documents["closeout-report.json"] = json.loads(
                closeout_report.read_text(encoding="utf-8")
            )
        if project.release:
            documents["release-record.json"] = {
                "project_id": project_id,
                "release": project.release,
            }

        paths = []
        for filename, payload in documents.items():
            path = output_dir / filename
            path.write_text(
                json.dumps(payload, ensure_ascii=True, indent=2),
                encoding="utf-8",
            )
            paths.append(path)
        return paths

    def _remap_import_paths(
        self,
        value: Any,
        *,
        source_project_id: str,
        target_output_dir: Path,
    ) -> Any:
        if isinstance(value, dict):
            return {
                key: self._remap_import_paths(
                    item,
                    source_project_id=source_project_id,
                    target_output_dir=target_output_dir,
                )
                for key, item in value.items()
            }
        if isinstance(value, list):
            return [
                self._remap_import_paths(
                    item,
                    source_project_id=source_project_id,
                    target_output_dir=target_output_dir,
                )
                for item in value
            ]
        if isinstance(value, str):
            normalized = value.replace("\\", "/")
            if source_project_id not in normalized or "/" not in normalized:
                return value
            path = Path(value)
            parts = list(path.parts)
            try:
                index = parts.index(source_project_id)
            except ValueError:
                return value
            suffix = Path(*parts[index + 1:])
            return str(target_output_dir / suffix)
        return value

    def _normalize_delivery_manifest(
        self,
        manifest: dict[str, Any],
        *,
        source_project_id: str,
        target_project_id: str,
        imported_package: str,
        governance: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        record = copy.deepcopy(manifest)
        governance = copy.deepcopy(governance or {})
        target_output_dir = self.output_root / target_project_id
        record = self._remap_import_paths(
            record,
            source_project_id=source_project_id,
            target_output_dir=target_output_dir,
        )
        governance = self._remap_import_paths(
            governance,
            source_project_id=source_project_id,
            target_output_dir=target_output_dir,
        )
        record["brief"]["project_id"] = target_project_id
        record["project_id"] = target_project_id
        packaged_subtitle = target_output_dir / "final_subtitles.srt"
        if packaged_subtitle.is_file():
            record["subtitle_srt"] = str(packaged_subtitle)
        packaged_audio = next(target_output_dir.glob("final_audio.*"), None)
        if packaged_audio and packaged_audio.is_file():
            record["audio_track"] = str(packaged_audio)
            if isinstance(record.get("audio_track_metadata"), dict):
                record["audio_track_metadata"]["uri"] = str(packaged_audio)
        for reference in record.get("reference_assets", []):
            if not isinstance(reference, dict):
                continue
            asset_id = str(reference.get("asset_id") or "")
            prefix = f"{source_project_id}:"
            if asset_id.startswith(prefix):
                reference["asset_id"] = (
                    f"{target_project_id}:{asset_id[len(prefix):]}"
                )
        record.pop("trace_id", None)
        if record.get("story_bible"):
            record["story_bible"]["project_id"] = target_project_id
        if record.get("policy"):
            record["policy"]["project_id"] = target_project_id
        if record.get("latest_evaluation"):
            record["latest_evaluation"]["project_id"] = target_project_id
        if record.get("latest_provider_benchmark"):
            record["latest_provider_benchmark"]["project_id"] = target_project_id
        if record.get("latest_comparison"):
            record["latest_comparison"]["project_id"] = target_project_id
        # Importing a package creates a new working copy. The source release
        # remains provenance in the package/audit files, but is not replayed.
        record["release"] = None
        record["deliveries"] = []
        record["delivery_feedback"] = []
        record["delivery_package"] = imported_package
        record["archive_package"] = None
        record["archive_verification_report"] = None
        policy_document = governance.get("policy-report.json") or {}
        record["policy_reports"] = copy.deepcopy(
            policy_document.get("reports")
            or (record.get("policy") or {}).get("reports", [])
        )

        evaluation_document = governance.get("evaluation-report.json") or {}
        evaluations = evaluation_document.get("history") or []
        if not evaluations and evaluation_document.get("latest"):
            evaluations = [evaluation_document["latest"]]
        record["evaluations"] = self._normalize_project_history(
            evaluations,
            target_project_id=target_project_id,
        )

        llmops_document = governance.get("llmops-report.json") or {}
        prompt_registry = llmops_document.get("prompt_registry") or {}
        prompt_versions = prompt_registry.get("prompts")
        if isinstance(prompt_versions, list) and prompt_versions:
            record["prompt_versions"] = copy.deepcopy(prompt_versions)
        annotations = llmops_document.get("annotations")
        record["evaluation_annotations"] = copy.deepcopy(
            annotations if isinstance(annotations, list) else []
        )
        experiments = llmops_document.get("experiments")
        record["experiments"] = copy.deepcopy(
            experiments if isinstance(experiments, list) else []
        )

        credentials_document = governance.get("content-credentials.json") or {}
        imported_credentials = []
        for raw_credential in credentials_document.get("credentials", []):
            if not isinstance(raw_credential, dict):
                continue
            credential = copy.deepcopy(raw_credential)
            credential["project_id"] = target_project_id
            for path_key in ("manifest_path", "c2pa_manifest_path", "signed_output"):
                source_path = credential.get(path_key)
                if source_path:
                    candidate = target_output_dir / "content-credentials" / Path(str(source_path)).name
                    credential[path_key] = str(candidate) if candidate.is_file() else None
            imported_credentials.append(credential)
        record["content_credentials"] = imported_credentials

        benchmark_document = governance.get("provider-benchmark.json") or {}
        benchmarks = benchmark_document.get("history") or []
        if not benchmarks and benchmark_document.get("latest"):
            benchmarks = [benchmark_document["latest"]]
        record["provider_benchmarks"] = self._normalize_project_history(
            benchmarks,
            target_project_id=target_project_id,
        )

        comparison_document = governance.get("shot-comparisons.json") or {}
        comparisons = comparison_document.get("reports") or []
        if not comparisons and comparison_document.get("latest"):
            comparisons = [comparison_document["latest"]]
        record["comparison_reports"] = self._normalize_project_history(
            comparisons,
            target_project_id=target_project_id,
        )
        record["comparison_count"] = len(record["comparison_reports"])

        audit_document = governance.get("audit-log.json") or {}
        source_integrity = (
            governance.get("audit-integrity.json")
            or audit_document.get("integrity")
        )
        source_events = audit_document.get("events") or []
        record["audit_events"] = []
        record["audit_chain_origin"] = "native"
        record["audit_anchors"] = []
        record["import_evidence"] = {
            "source_project_id": source_project_id,
            "source_audit_event_count": len(source_events),
            "source_audit_integrity": copy.deepcopy(source_integrity),
            "source_audit_anchors": copy.deepcopy(
                (governance.get("audit-anchors.json") or {}).get("anchors", [])
            ),
            "source_audit_log": str(target_output_dir / "audit-log.json"),
        }
        for shot_record in record.get("shots", []):
            shot = shot_record.get("shot", {})
            spec = shot_record.get("spec", {})
            shot["project_id"] = target_project_id
            spec["project_id"] = target_project_id
            shot_record["current_job_id"] = None
            if shot_record.get("current_artifact") is None and shot_record.get("artifact") is not None:
                shot_record["current_artifact"] = copy.deepcopy(shot_record["artifact"])
            shot_record["shot"] = shot
            shot_record["spec"] = spec
        return record

    @staticmethod
    def _normalize_project_history(
        history: list[dict[str, Any]],
        *,
        target_project_id: str,
    ) -> list[dict[str, Any]]:
        normalized = []
        for entry in history:
            item = copy.deepcopy(entry)
            if isinstance(item, dict):
                item["project_id"] = target_project_id
            normalized.append(item)
        return normalized

    def _project_from_record(self, record: dict[str, Any]) -> ProjectRuntime:
        project_trace_id = record.get("trace_id") or f"trace_{uuid4().hex[:16]}"
        project = ProjectRuntime(
            brief=CreativeBrief.model_validate(record["brief"]),
            trace_id=project_trace_id,
            story_bible=copy.deepcopy(record.get("story_bible")),
            source_documents=[
                NarrativeSourceDocument.model_validate(document)
                for document in record.get("source_documents", [])
            ],
            source_chapters=[
                NarrativeSourceChapter.model_validate(chapter)
                for chapter in record.get("source_chapters", [])
            ],
            narrative_event_candidates=[
                NarrativeEventCandidate.model_validate(candidate)
                for candidate in record.get("narrative_event_candidates", [])
            ],
            narrative_events=[
                NarrativeEvent.model_validate(event)
                for event in record.get("narrative_events", [])
            ],
            adaptation_scenes=[
                AdaptationScene.model_validate(scene)
                for scene in record.get("adaptation_scenes", [])
            ],
            reference_assets=self._stored_reference_assets(
                record.get("reference_assets", [])
            ),
            status=ProjectStatus(record.get("status", ProjectStatus.DRAFT)),
            created_at=datetime.fromisoformat(record["created_at"]),
            final_mp4=record.get("final_mp4"),
            lipsync_artifact=copy.deepcopy(record.get("lipsync_artifact")),
            subtitle_srt=record.get("subtitle_srt"),
            audio_track=record.get("audio_track"),
            audio_track_metadata=copy.deepcopy(record.get("audio_track_metadata")),
            dialogue_timeline=copy.deepcopy(record.get("dialogue_timeline")),
            delivery_package=record.get("delivery_package"),
            archive_package=record.get("archive_package"),
            release=copy.deepcopy(record.get("release")),
            archived_at=(
                datetime.fromisoformat(record["archived_at"])
                if record.get("archived_at")
                else None
            ),
            policy_reports=copy.deepcopy(record.get("policy_reports", [])),
            evaluations=copy.deepcopy(record.get("evaluations", [])),
            evaluation_baselines=copy.deepcopy(record.get("evaluation_baselines", [])),
            provider_benchmarks=copy.deepcopy(record.get("provider_benchmarks", [])),
            comparison_reports=copy.deepcopy(record.get("comparison_reports", [])),
            deliveries=copy.deepcopy(record.get("deliveries", [])),
            delivery_feedback=copy.deepcopy(record.get("delivery_feedback", [])),
            members=copy.deepcopy(record.get("members", [])),
            comments=copy.deepcopy(record.get("comments", [])),
            prompt_versions=copy.deepcopy(record.get("prompt_versions") or default_prompt_versions()),
            evaluation_annotations=copy.deepcopy(record.get("evaluation_annotations", [])),
            experiments=copy.deepcopy(record.get("experiments", [])),
            media_derivatives=copy.deepcopy(record.get("media_derivatives", {})),
            edit_timeline=copy.deepcopy(record.get("edit_timeline")),
            collaboration_presence=copy.deepcopy(record.get("collaboration_presence", [])),
            edit_locks=copy.deepcopy(record.get("edit_locks", [])),
            collaboration_documents=copy.deepcopy(record.get("collaboration_documents", {})),
            collaboration_events=copy.deepcopy(record.get("collaboration_events", [])),
            collaboration_event_sequence=int(record.get("collaboration_event_sequence", 0)),
            content_credentials=copy.deepcopy(record.get("content_credentials", [])),
            stage_locks=copy.deepcopy(record.get("stage_locks", {})),
            stage_invalidations=copy.deepcopy(record.get("stage_invalidations", [])),
            audit_events=[
                AuditEvent(
                    action=event.get("action", "legacy"),
                    actor=event.get("actor", "system"),
                    message=event.get("message", ""),
                    shot_id=event.get("shot_id"),
                    details=event.get("details", {}),
                    trace_id=event.get("trace_id") or project_trace_id,
                    occurred_at=datetime.fromisoformat(event["occurred_at"]),
                    sequence=event.get("sequence"),
                    previous_hash=event.get("previous_hash"),
                    event_hash=event.get("event_hash"),
                )
                for event in record.get("audit_events", [])
            ],
            audit_chain_origin=record.get(
                "audit_chain_origin",
                "native" if not record.get("audit_events") else "legacy_unsealed",
            ),
            import_evidence=copy.deepcopy(record.get("import_evidence")),
            audit_anchors=copy.deepcopy(record.get("audit_anchors", [])),
        )
        if not project.members:
            project.members = [
                {
                    "subject": "legacy-owner",
                    "role": "owner",
                    "added_at": project.created_at.isoformat(),
                }
            ]
        for shot_record in record.get("shots", []):
            runtime = ShotRuntime(
                shot=ShotCard.model_validate(shot_record["shot"]),
                spec=GenerationSpec.model_validate(shot_record["spec"]),
                review_status=ReviewStatus(
                    shot_record.get("review_status", ReviewStatus.PENDING)
                ),
                revision=int(shot_record.get("revision", 0)),
                current_job_id=shot_record.get("current_job_id"),
                current_artifact=copy.deepcopy(shot_record.get("current_artifact")),
                route=copy.deepcopy(shot_record.get("route")),
                quality=copy.deepcopy(shot_record.get("quality")),
                artifact_history=copy.deepcopy(
                    shot_record.get("artifact_history", [])
                ),
                variants=copy.deepcopy(shot_record.get("variants", [])),
                reviews=[
                    ReviewRecord(
                        status=ReviewStatus(review["status"]),
                        comment=review.get("comment", ""),
                        actor=review.get("actor", "reviewer"),
                        occurred_at=datetime.fromisoformat(review["occurred_at"]),
                    )
                    for review in shot_record.get("reviews", [])
                ],
            )
            project.shots[runtime.shot.shot_id] = runtime
        return project

    def _persist(self) -> None:
        with self._state_lock:
            payload = {
                "license_registry": {
                    "schema_version": self.license_registry.schema_version,
                    "source": self.license_registry.source,
                    "records": copy.deepcopy(self.license_registry.records),
                    "management": copy.deepcopy(self.license_registry_metadata),
                },
                "projects": [
                    self._project_record(project)
                    for project in self.projects.values()
                ],
                "jobs": self.jobs.as_list(),
                "workers": copy.deepcopy(self.workers),
            }
            serialized = json.dumps(payload, ensure_ascii=True, indent=2)
            if self.state_backend == "postgres":
                self.enterprise.postgres.save(serialized)
                return
            if self.state_backend == "sqlite":
                with sqlite3.connect(self.state_database_path) as connection:
                    connection.execute(
                        "CREATE TABLE IF NOT EXISTS mediaforge_state ("
                        "state_key TEXT PRIMARY KEY, payload TEXT NOT NULL, "
                        "updated_at TEXT NOT NULL)"
                    )
                    connection.execute(
                        "INSERT INTO mediaforge_state(state_key, payload, updated_at) "
                        "VALUES (?, ?, ?) ON CONFLICT(state_key) DO UPDATE SET "
                        "payload=excluded.payload, updated_at=excluded.updated_at",
                        ("workspace", serialized, datetime.now(timezone.utc).isoformat()),
                    )
                return
            temporary = self.state_path.with_suffix(".tmp")
            temporary.write_text(serialized, encoding="utf-8")
            for attempt in range(5):
                try:
                    temporary.replace(self.state_path)
                    return
                except PermissionError:
                    if attempt == 4:
                        raise
                    sleep(0.05 * (attempt + 1))

    def _load_state(self) -> None:
        if self.state_backend == "postgres":
            try:
                serialized = self.enterprise.postgres.load()
                if serialized is None:
                    return
                payload = json.loads(serialized)
            except Exception as exc:
                raise WorkflowError("cannot restore MediaForge state from PostgreSQL") from exc
        elif self.state_backend == "sqlite":
            if not self.state_database_path.exists():
                return
            try:
                with sqlite3.connect(self.state_database_path) as connection:
                    row = connection.execute(
                        "SELECT payload FROM mediaforge_state WHERE state_key = ?",
                        ("workspace",),
                    ).fetchone()
                if not row:
                    return
                payload = json.loads(row[0])
            except (OSError, sqlite3.Error, ValueError, TypeError, json.JSONDecodeError) as exc:
                raise WorkflowError(
                    f"cannot restore MediaForge state from {self.state_database_path}: {exc}"
                ) from exc
        else:
            if not self.state_path.exists():
                return
            try:
                payload = json.loads(self.state_path.read_text(encoding="utf-8"))
            except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
                raise WorkflowError(
                    f"cannot restore MediaForge state from {self.state_path}: {exc}"
                ) from exc
        try:
            self.jobs.restore(payload.get("jobs", []))
            stored_workers = payload.get("workers", {})
            if isinstance(stored_workers, dict):
                self.workers = {
                    str(worker_id): copy.deepcopy(worker)
                    for worker_id, worker in stored_workers.items()
                    if isinstance(worker, dict)
                }
            migrated = False
            registry_payload = payload.get("license_registry")
            if isinstance(registry_payload, dict) and isinstance(
                registry_payload.get("records"), list
            ):
                self.license_registry = LicenseRegistry.from_payload(
                    registry_payload,
                    source=str(registry_payload.get("source") or "persisted state"),
                )
                metadata = registry_payload.get("management")
                if isinstance(metadata, dict):
                    self.license_registry_metadata = {
                        "updated_at": metadata.get("updated_at"),
                        "updated_by": metadata.get("updated_by"),
                        "change_id": metadata.get("change_id"),
                    }
                    if isinstance(metadata.get("sync"), dict):
                        self.license_registry_metadata["sync"] = copy.deepcopy(
                            metadata["sync"]
                        )
            for job in self.jobs.all():
                migrated = self._backfill_job_artifact_metadata(job) or migrated
            for record in payload.get("projects", []):
                project = self._project_from_record(record)
                self.projects[project.brief.project_id] = project
                migrated = self._backfill_project_artifact_metadata(project) or migrated
            can_write_startup_migrations = (
                not self.enterprise.control_plane.enabled
                or self.enterprise.control_plane.primary
            )
            if migrated and can_write_startup_migrations:
                self._persist()
            if can_write_startup_migrations:
                self._recover_stale_jobs(actor="startup-recovery")
        except (OSError, ValueError, TypeError, json.JSONDecodeError, sqlite3.Error) as exc:
            raise WorkflowError(
                f"cannot restore MediaForge state from {self.state_backend} storage: {exc}"
            ) from exc

    def _backfill_job_artifact_metadata(self, job: JobRecord) -> bool:
        changed = False
        for artifact in job.artifacts:
            if artifact.metadata_uri:
                changed = self._upgrade_metadata_sidecar(
                    artifact.metadata_uri,
                    provider=self.provider.name,
                    job_id=artifact.job_id,
                    spec=job.spec.model_dump(mode="json"),
                ) or changed
                continue
            metadata_uri = self._write_legacy_metadata_sidecar(
                artifact.uri,
                provider=self.provider.name,
                job_id=artifact.job_id,
                spec=job.spec.model_dump(mode="json"),
            )
            if not metadata_uri:
                continue
            artifact.metadata_uri = metadata_uri
            changed = True
        return changed

    def _backfill_project_artifact_metadata(self, project: ProjectRuntime) -> bool:
        changed = False
        for runtime in project.shots.values():
            entries = [
                *runtime.artifact_history,
                *runtime.variants,
                {"artifact": runtime.current_artifact},
            ]
            for entry in entries:
                artifact = entry.get("artifact") if isinstance(entry, dict) else None
                if not isinstance(artifact, dict):
                    continue
                if artifact.get("metadata_uri"):
                    changed = self._upgrade_metadata_sidecar(
                        artifact["metadata_uri"],
                        provider=(entry.get("route") or {}).get(
                            "provider", self.provider.name
                        ),
                        job_id=entry.get("job_id") or artifact.get("job_id"),
                        spec=runtime.spec.model_dump(mode="json"),
                    ) or changed
                    continue
                metadata_uri = self._write_legacy_metadata_sidecar(
                    artifact.get("uri"),
                    provider=(entry.get("route") or {}).get(
                        "provider", self.provider.name
                    ),
                    job_id=entry.get("job_id") or artifact.get("job_id"),
                    spec=runtime.spec.model_dump(mode="json"),
                )
                if not metadata_uri:
                    continue
                artifact["metadata_uri"] = metadata_uri
                changed = True
        return changed

    def _write_legacy_metadata_sidecar(
        self,
        artifact_uri: Any,
        *,
        provider: Any,
        job_id: Any,
        spec: dict[str, Any],
    ) -> str | None:
        if not artifact_uri:
            return None
        artifact_path = Path(str(artifact_uri))
        try:
            resolved = artifact_path.resolve()
            output_root = self.output_root.resolve()
        except OSError:
            return None
        if output_root != resolved and output_root not in resolved.parents:
            return None
        if not resolved.is_file():
            return None
        metadata_path = resolved.with_suffix(".json")
        if metadata_path.is_file():
            self._upgrade_metadata_sidecar(
                metadata_path,
                provider=provider,
                job_id=job_id,
                spec=spec,
            )
            return str(metadata_path)
        payload = {
            "schema_version": "mediaforge-artifact-metadata-v1",
            "provider": str(provider or self.provider.name),
            "job_id": str(job_id or "legacy-job"),
            "workflow_template": (spec.get("workflow") or {}).get("template_id"),
            "spec": spec,
            "migration": "legacy-state-backfill",
        }
        try:
            metadata_path.write_text(
                json.dumps(payload, ensure_ascii=True, indent=2),
                encoding="utf-8",
            )
        except OSError:
            return None
        return str(metadata_path)

    @staticmethod
    def _upgrade_metadata_sidecar(
        metadata_uri: Any,
        *,
        provider: Any,
        job_id: Any,
        spec: dict[str, Any],
    ) -> bool:
        metadata_path = Path(str(metadata_uri or ""))
        if not metadata_path.is_file():
            return False
        try:
            payload = json.loads(metadata_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            return False
        if not isinstance(payload, dict):
            return False
        if payload.get("schema_version") == "mediaforge-artifact-metadata-v1":
            return False
        payload["schema_version"] = "mediaforge-artifact-metadata-v1"
        payload.setdefault("provider", str(provider or "legacy-provider"))
        payload.setdefault("job_id", str(job_id or "legacy-job"))
        payload.setdefault(
            "workflow_template",
            (spec.get("workflow") or {}).get("template_id"),
        )
        try:
            metadata_path.write_text(
                json.dumps(payload, ensure_ascii=True, indent=2),
                encoding="utf-8",
            )
        except OSError:
            return False
        return True

    def _project_record(self, project: ProjectRuntime) -> dict[str, Any]:
        closeout_report = self._closeout_report(project.brief.project_id)
        acceptance_report = self._acceptance_report(project.brief.project_id)
        archive_verification_report = self._archive_verification_report(
            project.brief.project_id
        )
        delivery_verification_report = self._delivery_verification_report(
            project.brief.project_id
        )
        return {
            "brief": project.brief.model_dump(mode="json"),
            "trace_id": project.trace_id,
            "story_bible": project.story_bible,
            "source_documents": [
                self._source_document_view(document)
                for document in project.source_documents
            ],
            "source_chapters": [
                self._source_chapter_view(chapter)
                for chapter in self._ordered_source_chapters(project)
            ],
            "narrative_event_candidates": [
                self._narrative_event_candidate_view(candidate)
                for candidate in self._ordered_narrative_event_candidates(project)
            ],
            "narrative_events": [
                self._narrative_event_view(event)
                for event in self._ordered_narrative_events(project)
            ],
            "adaptation_scenes": [
                self._adaptation_scene_view(scene)
                for scene in self._ordered_adaptation_scenes(project)
            ],
            "reference_assets": copy.deepcopy(project.reference_assets),
            "members": copy.deepcopy(project.members),
            "comments": copy.deepcopy(project.comments),
            "prompt_versions": copy.deepcopy(project.prompt_versions),
            "evaluation_annotations": copy.deepcopy(project.evaluation_annotations),
            "experiments": copy.deepcopy(project.experiments),
            "media_derivatives": copy.deepcopy(project.media_derivatives),
            "edit_timeline": copy.deepcopy(project.edit_timeline),
            "collaboration_presence": copy.deepcopy(project.collaboration_presence),
            "edit_locks": copy.deepcopy(project.edit_locks),
            "collaboration_documents": copy.deepcopy(project.collaboration_documents),
            "collaboration_events": copy.deepcopy(project.collaboration_events),
            "collaboration_event_sequence": project.collaboration_event_sequence,
            "content_credentials": copy.deepcopy(project.content_credentials),
            "stage_locks": copy.deepcopy(project.stage_locks),
            "stage_invalidations": copy.deepcopy(project.stage_invalidations),
            "audit_events": [
                self._audit_event_view(event)
                for event in project.audit_events
            ],
            "audit_chain_origin": project.audit_chain_origin,
            "import_evidence": copy.deepcopy(project.import_evidence),
            "audit_anchors": copy.deepcopy(project.audit_anchors),
            "status": project.status,
            "created_at": project.created_at.isoformat(),
            "final_mp4": project.final_mp4,
            "lipsync_artifact": copy.deepcopy(project.lipsync_artifact),
            "subtitle_srt": project.subtitle_srt,
            "audio_track": project.audio_track,
            "audio_track_metadata": copy.deepcopy(project.audio_track_metadata),
            "dialogue_timeline": copy.deepcopy(project.dialogue_timeline),
            "delivery_package": project.delivery_package,
            "archive_package": project.archive_package,
            "archive_verification_report": (
                str(archive_verification_report)
                if archive_verification_report.is_file()
                else None
            ),
            "release": project.release,
            "closeout_report": (
                str(closeout_report) if closeout_report.is_file() else None
            ),
            "acceptance_report": (
                str(acceptance_report) if acceptance_report.is_file() else None
            ),
            "delivery_verification_report": (
                str(delivery_verification_report)
                if delivery_verification_report.is_file()
                else None
            ),
            "archived_at": (
                project.archived_at.isoformat()
                if project.archived_at
                else None
            ),
            "policy_reports": project.policy_reports,
            "evaluations": project.evaluations,
            "evaluation_baselines": project.evaluation_baselines,
            "provider_benchmarks": project.provider_benchmarks,
            "comparison_reports": project.comparison_reports,
            "deliveries": project.deliveries,
            "delivery_feedback": project.delivery_feedback,
            "shots": [
                {
                    "shot": runtime.shot.model_dump(mode="json"),
                    "spec": runtime.spec.model_dump(mode="json"),
                    "review_status": runtime.review_status,
                    "revision": runtime.revision,
                    "current_job_id": runtime.current_job_id,
                    "current_artifact": runtime.current_artifact,
                    "route": runtime.route,
                    "quality": runtime.quality,
                    "artifact_history": runtime.artifact_history,
                    "variants": runtime.variants,
                    "reviews": [
                        {
                            "status": review.status,
                            "comment": review.comment,
                            "actor": review.actor,
                            "occurred_at": review.occurred_at.isoformat(),
                        }
                        for review in runtime.reviews
                    ],
                }
                for runtime in project.shots.values()
            ],
        }

    @staticmethod
    def _shot(project: ProjectRuntime, shot_id: str) -> ShotRuntime:
        try:
            return project.shots[shot_id]
        except KeyError as exc:
            raise ShotNotFound(shot_id) from exc

    @staticmethod
    def _variant(runtime: ShotRuntime, variant_id: str) -> dict[str, Any]:
        for variant in runtime.variants:
            if variant.get("variant_id") == variant_id:
                return variant
        raise WorkflowError(f"unknown variant: {variant_id}")

    @staticmethod
    def _build_shots(
        brief: CreativeBrief,
        narrative_events: list[NarrativeEvent] | None = None,
        adaptation_scenes: list[AdaptationScene] | None = None,
    ) -> list[ShotCard]:
        if adaptation_scenes:
            ordered_scenes = sorted(
                adaptation_scenes,
                key=lambda scene: (scene.sequence, scene.scene_id),
            )
            target_duration = brief.duration_seconds
            shot_count = (target_duration + 4) // 5
            selected_scenes = [
                ordered_scenes[(index * len(ordered_scenes)) // shot_count]
                for index in range(shot_count)
            ]
            occurrence_count: dict[str, int] = {}
            for scene in selected_scenes:
                occurrence_count[scene.scene_id] = occurrence_count.get(scene.scene_id, 0) + 1
            occurrence_index: dict[str, int] = {}
            base_duration, remainder = divmod(target_duration, shot_count)
            rows: list[ShotCard] = []
            for index, scene in enumerate(selected_scenes, start=1):
                occurrence_index[scene.scene_id] = occurrence_index.get(scene.scene_id, 0) + 1
                beat_index = occurrence_index[scene.scene_id] - 1
                beat = scene.beats[beat_index % len(scene.beats)] if scene.beats else scene.synopsis
                dialogue = f" 对白：{scene.dialogue_draft}" if scene.dialogue_draft else ""
                suffix = (
                    f" · 场次节拍 {beat_index + 1}/{occurrence_count[scene.scene_id]}"
                    if occurrence_count[scene.scene_id] > 1
                    else ""
                )
                rows.append(
                    ShotCard(
                        project_id=brief.project_id,
                        shot_id=f"ep01_scene{scene.sequence:03d}_{index:02d}",
                        scene=scene.heading,
                        description=f"{scene.synopsis} {beat}{dialogue}{suffix}".strip(),
                        characters=list(scene.characters),
                        duration_seconds=base_duration + (1 if index <= remainder else 0),
                        mood=scene.mood or "dramatic",
                        subtitle_text=(scene.dialogue_draft[:500] or None),
                    )
                )
            return rows
        if narrative_events:
            ordered_events = sorted(
                narrative_events,
                key=lambda event: (
                    event.chapter_number,
                    event.sequence,
                    event.event_id,
                ),
            )
            target_duration = brief.duration_seconds
            shot_count = (target_duration + 4) // 5
            base_duration, remainder = divmod(target_duration, shot_count)
            selected_events = [
                ordered_events[(index * len(ordered_events)) // shot_count]
                for index in range(shot_count)
            ]
            occurrence_count: dict[str, int] = {}
            for event in selected_events:
                occurrence_count[event.event_id] = occurrence_count.get(event.event_id, 0) + 1
            occurrence_index: dict[str, int] = {}
            fallback_moods = {
                "MAINLINE": "tense",
                "SUPPORTING": "reflective",
                "TRANSITION": "quiet",
            }
            rows: list[ShotCard] = []
            for index, event in enumerate(selected_events, start=1):
                occurrence_index[event.event_id] = occurrence_index.get(event.event_id, 0) + 1
                beat = occurrence_index[event.event_id]
                suffix = (
                    f" · 叙事节拍 {beat}/{occurrence_count[event.event_id]}"
                    if occurrence_count[event.event_id] > 1
                    else ""
                )
                rows.append(
                    ShotCard(
                        project_id=brief.project_id,
                        shot_id=(
                            f"ep01_ch{event.chapter_number:03d}_"
                            f"ev{event.sequence:03d}_{index:02d}"
                        ),
                        scene=event.scene,
                        description=f"{event.summary}{suffix}",
                        characters=list(event.characters),
                        duration_seconds=base_duration + (1 if index <= remainder else 0),
                        mood=(
                            event.emotions[0]
                            if event.emotions
                            else fallback_moods.get(str(event.importance), "tense")
                        ),
                    )
                )
            return rows

        first, second = brief.characters[:2]
        rows = [
            ("sc01", "公寓客厅", f"{first} 在深夜接起陌生电话。", [first], "tense"),
            ("sc01", "公寓客厅", f"{second} 的身影出现在窗外。", [first, second], "surprised"),
            ("sc02", "楼道", f"{first} 追出门，却只看到闪烁的感应灯。", [first], "fearful"),
            ("sc02", "楼道", f"{second} 留下一张写着警告的旧照片。", [second], "mysterious"),
            ("sc03", "公寓客厅", f"{first} 发现照片背面有自己的签名。", [first], "shocked"),
            ("sc03", "公寓客厅", f"{first} 再次接通电话，听见自己的声音。", [first], "resolved"),
            ("sc04", "楼道", f"{first} 沿着脚步声走向安全出口。", [first], "urgent"),
            ("sc04", "楼道", f"{second} 在门后说出只有他们知道的暗号。", [first, second], "uncertain"),
            ("sc05", "屋顶", f"{first} 看到城市灯光下另一部正在响起的电话。", [first], "quiet"),
            ("sc05", "屋顶", f"{second} 终于承认这通电话来自未来。", [second], "revealing"),
            ("sc06", "公寓客厅", f"{first} 把旧照片放回桌面，决定面对真相。", [first], "determined"),
            ("sc06", "公寓客厅", f"{first} 与第二部电话同时接通，故事留下新的悬念。", [first, second], "open"),
        ]
        target_duration = brief.duration_seconds
        shot_count = (target_duration + 4) // 5
        return [
            ShotCard(
                project_id=brief.project_id,
                shot_id=f"ep01_{scene_id}_{index:02d}",
                scene=scene,
                description=description,
                characters=characters,
                duration_seconds=min(5, target_duration - ((index - 1) * 5)),
                mood=mood,
            )
            for index, (scene_id, scene, description, characters, mood) in enumerate(
                rows[:shot_count],
                start=1,
            )
        ]

    def _preferred_capability(self) -> Capability:
        supported: set[Capability] = set()
        raw_capabilities = self.provider_status.get("capabilities") or []
        mode = str(self.provider_status.get("mode") or "").strip().lower()
        if mode == "comfyui" and isinstance(raw_capabilities, list):
            for capability in raw_capabilities:
                try:
                    supported.add(
                        capability
                        if isinstance(capability, Capability)
                        else Capability(capability)
                    )
                except ValueError:
                    continue
        if not supported:
            for capability in (Capability.IMAGE_GENERATION, Capability.IMAGE_TO_VIDEO):
                try:
                    if self.provider.supports(capability):
                        supported.add(capability)
                except Exception:
                    continue
        if (
            mode == "comfyui"
            and Capability.IMAGE_GENERATION in supported
            and Capability.IMAGE_TO_VIDEO not in supported
        ):
            return Capability.IMAGE_GENERATION
        if Capability.IMAGE_TO_VIDEO in supported and Capability.IMAGE_GENERATION not in supported:
            return Capability.IMAGE_TO_VIDEO
        if Capability.IMAGE_GENERATION in supported and Capability.IMAGE_TO_VIDEO not in supported:
            return Capability.IMAGE_GENERATION
        if Capability.IMAGE_TO_VIDEO in supported:
            return Capability.IMAGE_TO_VIDEO
        if Capability.IMAGE_GENERATION in supported:
            return Capability.IMAGE_GENERATION
        return Capability.IMAGE_TO_VIDEO

    def _workflow_template_for_capability(self, capability: Capability) -> str:
        """Return the platform-selected reviewed template for a generation job."""
        details = self.provider_status.get("details") or {}
        mode = str(self.provider_status.get("mode") or "").strip().lower()
        if capability == Capability.IMAGE_GENERATION:
            selected = str(details.get("default_template_id") or "").strip()
            if selected:
                return selected
            configured = os.getenv("MEDIAFORGE_IMAGE_WORKFLOW_TEMPLATE_ID", "").strip()
            if configured:
                return configured
            if mode == "comfyui":
                return "comfyui_image:v1"
            return "p0_mock_i2v:v1"
        selected = str(details.get("default_video_template_id") or "").strip()
        if selected:
            return selected
        configured = os.getenv("MEDIAFORGE_VIDEO_WORKFLOW_TEMPLATE_ID", "").strip()
        if configured:
            return configured
        if mode == "replicate":
            return "replicate_i2v:v1"
        return "p0_mock_i2v:v1"

    def _approved_workflow_template_roots(self) -> set[str]:
        """Return built-ins plus roots from configured reviewed ComfyUI registries.

        Registry membership proves that an operator reviewed a graph and its pin;
        it intentionally does not replace the separate license-registry check in
        ``project_compliance``. A custom root can therefore pass this execution
        allowlist but still block release until governance records its rights.
        """
        roots = {
            "comfyui_image",
            "p0_mock_i2v",
            "p1_mock_i2v",
            "provider_probe",
            "replicate_i2v",
        }
        statuses = self.provider_statuses or [self.provider_status]
        for status in statuses:
            if not bool(status.get("configured")):
                continue
            if str(status.get("mode") or "").strip().lower() != "comfyui":
                continue
            details = status.get("details")
            if not isinstance(details, dict):
                continue
            template_ids = [details.get("default_template_id")]
            workflow = details.get("workflow")
            if isinstance(workflow, dict):
                template_ids.extend(
                    [
                        workflow.get("registry_template_id"),
                        workflow.get("requested_template_id"),
                    ]
                )
            registry = details.get("workflow_registry")
            if isinstance(registry, dict):
                for entry in registry.get("workflows") or []:
                    if isinstance(entry, dict):
                        template_ids.extend(
                            [
                                entry.get("registry_template_id"),
                                entry.get("requested_template_id"),
                            ]
                        )
            for template_id in template_ids:
                root = str(template_id or "").strip().split(":", 1)[0]
                if root:
                    roots.add(root)
        return roots

    @staticmethod
    def _stored_reference_assets(records: Any) -> list[dict[str, Any]]:
        normalized: list[dict[str, Any]] = []
        if not isinstance(records, list):
            return normalized
        for record in records:
            if not isinstance(record, dict):
                raise WorkflowError("reference asset record must be an object")
            payload = {
                key: value
                for key, value in record.items()
                if key not in {"used_by_shots", "derivatives"}
            }
            try:
                normalized.append(
                    ReferenceAssetRef.model_validate(payload).model_dump(
                        mode="json"
                    )
                )
            except ValueError as exc:
                raise WorkflowError("reference asset record is invalid") from exc
        return normalized

    def _copy_reference_assets(
        self,
        records: Any,
        *,
        source_project_id: str,
        target_project_id: str,
    ) -> list[dict[str, Any]]:
        normalized = self._stored_reference_assets(records)
        source_root = (self.output_root / source_project_id).resolve()
        target_root = self.output_root / target_project_id
        target_dir = target_root / "references"
        target_dir.mkdir(parents=True, exist_ok=True)
        copied: list[dict[str, Any]] = []
        for record in normalized:
            item = copy.deepcopy(record)
            asset_id = str(item.get("asset_id") or "")
            prefix = f"{source_project_id}:"
            if asset_id.startswith(prefix):
                item["asset_id"] = f"{target_project_id}:{asset_id[len(prefix):]}"
            uri = item.get("uri")
            if uri:
                source_path = Path(str(uri))
                try:
                    resolved = source_path.resolve()
                except OSError:
                    resolved = source_path
                if source_root in resolved.parents and resolved.is_file():
                    target_path = target_dir / resolved.name
                    if target_path.exists():
                        target_path = target_dir / f"{uuid4().hex[:16]}_{resolved.name}"
                    target_path.write_bytes(resolved.read_bytes())
                    item["uri"] = str(target_path)
                    item["size_bytes"] = target_path.stat().st_size
                    item["sha256"] = sha256_file(target_path)
            copied.append(item)
        return copied

    def _copy_source_documents(
        self,
        records: Any,
        *,
        source_project_id: str,
        target_project_id: str,
        source_document_blobs: Any = None,
    ) -> list[NarrativeSourceDocument]:
        if not isinstance(records, list):
            raise WorkflowError("source document records must be a list")
        blobs = source_document_blobs if isinstance(source_document_blobs, dict) else {}
        target_dir = self.output_root / target_project_id / "sources"
        target_dir.mkdir(parents=True, exist_ok=True)
        copied: list[NarrativeSourceDocument] = []
        for raw_document in records:
            try:
                document = NarrativeSourceDocument.model_validate(raw_document)
            except ValueError as exc:
                raise WorkflowError("source document record is invalid") from exc
            encoded = blobs.get(document.document_id)
            if encoded is not None:
                if not isinstance(encoded, str):
                    raise WorkflowError("source document snapshot content is invalid")
                try:
                    content = base64.b64decode(encoded, validate=True)
                except (ValueError, TypeError) as exc:
                    raise WorkflowError("source document snapshot content is invalid") from exc
            else:
                content = self._verified_source_document_path(
                    source_project_id,
                    document,
                ).read_bytes()
            if len(content) != document.size_bytes:
                raise WorkflowError("source document size check failed during copy")
            if hashlib.sha256(content).hexdigest() != document.sha256:
                raise WorkflowError("source document integrity check failed during copy")
            target_path = target_dir / f"{document.document_id}.{document.format}"
            target_path.write_bytes(content)
            copied.append(document.model_copy(update={"uri": str(target_path)}))
        return copied

    def _copy_audio_track(
        self,
        record: dict[str, Any],
        *,
        source_project_id: str,
        target_project_id: str,
    ) -> tuple[str | None, dict[str, Any] | None]:
        source_uri = record.get("audio_track")
        if not source_uri:
            return None, None
        source_root = (self.output_root / source_project_id).resolve()
        source_path = Path(str(source_uri))
        try:
            resolved = source_path.resolve()
        except OSError as exc:
            raise WorkflowError("audio track path is invalid") from exc
        if source_root != resolved and source_root not in resolved.parents:
            raise WorkflowError("audio track must be inside the source project")
        if not resolved.is_file():
            raise WorkflowError(f"audio track is missing: {source_path}")

        target_dir = self.output_root / target_project_id / "audio"
        target_dir.mkdir(parents=True, exist_ok=True)
        target_path = target_dir / f"soundtrack{resolved.suffix.lower()}"
        if target_path.exists():
            target_path = target_dir / f"{uuid4().hex[:16]}_soundtrack{resolved.suffix.lower()}"
        target_path.write_bytes(resolved.read_bytes())
        metadata = copy.deepcopy(record.get("audio_track_metadata") or {})
        metadata["uri"] = str(target_path)
        metadata["size_bytes"] = target_path.stat().st_size
        metadata["sha256"] = sha256_file(target_path)
        metadata.setdefault("format", resolved.suffix.lower().lstrip("."))
        return str(target_path), metadata

    def _refresh_reference_specs(self, project: ProjectRuntime) -> None:
        for runtime in project.shots.values():
            references = self._reference_assets_for_shot(project, runtime.shot)
            asset_versions = dict(runtime.spec.asset_versions)
            for reference in references:
                if reference.get("character"):
                    asset_versions[reference["character"]] = (
                        f"{reference['asset_id']}:{reference['version']}"
                    )
            runtime.spec = runtime.spec.model_copy(
                update={
                    "asset_versions": asset_versions,
                    "reference_assets": [
                        ReferenceAssetRef.model_validate(reference)
                        for reference in references
                    ],
                }
            )

    def _reference_assets_for_shot(
        self,
        project: ProjectRuntime,
        shot: ShotCard,
    ) -> list[dict[str, Any]]:
        references: list[dict[str, Any]] = []
        for character in shot.characters:
            selected = next(
                (
                    asset
                    for asset in reversed(project.reference_assets)
                    if asset.get("kind") == "character_reference"
                    and asset.get("character") == character
                ),
                None,
            )
            references.append(copy.deepcopy(selected) if selected else {
                "asset_id": f"{project.brief.project_id}:character:{character}:v1",
                "kind": "character_reference",
                "name": character,
                "version": "v1",
                "character": character,
                "source": "creative_brief",
                "license": "user_supplied_or_project_owned",
            })
        for asset in project.reference_assets:
            if asset.get("kind") != "character_reference":
                references.append(copy.deepcopy(asset))
        return references

    def _compile_spec(
        self,
        shot: ShotCard,
        *,
        reference_assets: list[dict[str, Any]] | None = None,
    ) -> GenerationSpec:
        references = reference_assets or [
            {
                "asset_id": f"{shot.project_id}:character:{character}:v1",
                "kind": "character_reference",
                "name": character,
                "version": "v1",
                "character": character,
                "source": "creative_brief",
                "license": "user_supplied_or_project_owned",
            }
            for character in shot.characters
        ]
        capability = self._preferred_capability()
        return GenerationSpec(
            project_id=shot.project_id,
            shot_id=shot.shot_id,
            asset_versions={
                reference["character"]: (
                    f"{reference['asset_id']}:{reference['version']}"
                )
                for reference in references
                if reference.get("character")
            },
            reference_assets=[
                ReferenceAssetRef.model_validate(reference)
                for reference in references
            ],
            intent=Intent(
                shot_type="medium_close_up",
                camera_motion="slow_push_in",
                duration_seconds=float(shot.duration_seconds),
                mood=shot.mood,
            ),
            provider_constraints=ProviderConstraints(
                capability=capability,
                resolution="720p",
                max_cost=0.40,
                deadline_seconds=180,
            ),
            workflow=WorkflowSpec(
                template_id=self._workflow_template_for_capability(capability),
                allowed_lora_ids=["cinematic_style:v1"],
                controlnet=ControlNet(enabled=True, strength=0.65),
            ),
            quality_requirements=QualityRequirements(
                minimum_character_similarity=0.80,
                must_not_include=["watermark", "extra_face"],
            ),
        )
