from __future__ import annotations

import asyncio
import json
import os
from contextlib import asynccontextmanager
from time import perf_counter
from pathlib import Path
from typing import Any, Literal

from fastapi import FastAPI, HTTPException, Query, Request, Response, WebSocket, WebSocketDisconnect, status
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field

from .callback_security import CallbackSecurity, CallbackSecurityError
from .replicate_webhook import (
    ReplicateWebhookSecurity,
    ReplicateWebhookSecurityError,
)
from .auth import AuthManager, AuthenticationError, Principal
from .settlement_callback import SettlementCallbackError, SettlementCallbackSecurity
from .stripe_settlement import StripeSettlementWebhook
from .config import build_provider_bundles_from_env
from .contracts import (
    AdaptationSceneInput,
    CreativeBrief,
    DialogueLine,
    JobStatus,
    NarrativeEventInput,
    NarrativeSourceChapterInput,
    ReviewStatus,
)
from .enterprise_runtime import ControlPlaneUnavailable, EnterpriseConfigurationError
from .llm import StoryPlannerError, build_story_planner_from_env
from .memory import MemoryUnavailable
from .planning import PlanningError, PlanningNotFound, PlanningBusy
from .observability import HttpMetrics, MetricsAccessPolicy
from .quotas import QuotaViolation
from .service import (
    DeliveryNotFound,
    JobNotFound,
    MediaForgeService,
    PolicyViolation,
    ProjectNotFound,
    ShotNotFound,
    TenantViolation,
    WorkflowError,
)
from .registry_sync import RegistrySyncError
from .router import ProviderRegistration
from .speech import SpeechSynthesisError


class ReviewRequest(BaseModel):
    status: Literal["APPROVED", "CHANGES_REQUESTED"]
    comment: str = Field(default="", max_length=2000)
    actor: str = Field(default="reviewer", min_length=1, max_length=120)


class PlanningReviewRequest(BaseModel):
    decision: Literal['approve', 'revise', 'reject']
    comment: str = Field(default='', max_length=2000)


class RevisionRequest(BaseModel):
    comment: str = Field(default="", max_length=2000)


class BatchApprovalRequest(BaseModel):
    comment: str = Field(default="Batch approved in Studio.", max_length=2000)
    actor: str = Field(default="studio-reviewer", min_length=1, max_length=120)


class ReleaseRequest(BaseModel):
    channel: str = Field(default="internal-review", min_length=1, max_length=120)
    comment: str = Field(default="", max_length=2000)
    actor: str = Field(default="studio-publisher", min_length=1, max_length=120)


class DeliveryRequest(BaseModel):
    channel: str = Field(default="internal-review", min_length=1, max_length=120)
    recipient: str = Field(default="studio-archive", min_length=1, max_length=120)
    destination_uri: str | None = Field(default=None, max_length=2000)
    note: str = Field(default="", max_length=2000)
    actor: str = Field(default="delivery-ops", min_length=1, max_length=120)


class DeliveryDispatchRequest(DeliveryRequest):
    pass


class BillingEventRequest(BaseModel):
    event_id: str = Field(min_length=1, max_length=180)
    category: str = Field(min_length=1, max_length=120)
    quantity: float = Field(ge=0, allow_inf_nan=False)
    unit_price: float = Field(ge=0, allow_inf_nan=False)
    project_id: str | None = Field(default=None, max_length=120)
    currency: str = Field(default="USD", min_length=3, max_length=3)
    metadata: dict[str, Any] = Field(default_factory=dict)


class BillingSettlementRequest(BaseModel):
    settlement_id: str = Field(min_length=1, max_length=180)
    provider: str = Field(min_length=1, max_length=120)
    status: Literal["pending", "paid", "failed", "refunded", "void"] = "pending"
    amount: float = Field(ge=0, allow_inf_nan=False)
    currency: str = Field(default="USD", min_length=3, max_length=3)
    external_id: str | None = Field(default=None, max_length=240)
    metadata: dict[str, Any] = Field(default_factory=dict)


class BillingSettlementCallbackRequest(BillingSettlementRequest):
    model_config = ConfigDict(extra="forbid")

    tenant_id: str = Field(min_length=1, max_length=160)


class RAGFlowWriteSyncRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    confirm_data_export: Literal[True]


class DeliveryAcknowledgeRequest(BaseModel):
    accepted: bool = True
    note: str = Field(default="", max_length=2000)
    actor: str = Field(default="delivery-recipient", min_length=1, max_length=120)


class DeliveryFeedbackRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    delivery_id: str = Field(min_length=1, max_length=160)
    target_type: Literal["project", "shot"] = "project"
    target_id: str | None = Field(default=None, max_length=160)
    category: Literal[
        "story", "visual", "audio", "continuity", "timing", "brand", "other"
    ] = "other"
    severity: Literal["LOW", "NORMAL", "HIGH", "BLOCKER"] = "NORMAL"
    verdict: Literal["APPROVE", "REQUEST_CHANGES", "QUESTION"] = "REQUEST_CHANGES"
    comment: str = Field(min_length=1, max_length=4_000)
    rating: float | None = Field(default=None, ge=1, le=5, allow_inf_nan=False)
    assignee: str | None = Field(default=None, max_length=160)
    actor: str = Field(default="delivery-recipient", min_length=1, max_length=120)


class DeliveryFeedbackTriageRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["ACKNOWLEDGED", "RESOLVED", "DISMISSED"]
    resolution: str = Field(default="", max_length=4_000)
    assignee: str | None = Field(default=None, max_length=160)
    actor: str = Field(default="delivery-owner", min_length=1, max_length=120)


class CloneRequest(BaseModel):
    project_id: str = Field(
        min_length=1,
        max_length=64,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]*$",
    )
    title_suffix: str = Field(default="Branch", max_length=120)
    actor: str = Field(default="studio-user", min_length=1, max_length=120)


class ProjectImportRequest(BaseModel):
    snapshot: dict[str, Any]
    project_id: str | None = Field(
        default=None,
        min_length=1,
        max_length=64,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]*$",
    )
    actor: str = Field(default="studio-user", min_length=1, max_length=120)


class ProjectUpdateRequest(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=300)
    premise: str | None = Field(default=None, min_length=1, max_length=5000)
    genre: str | None = Field(default=None, min_length=1, max_length=120)
    style: str | None = Field(default=None, min_length=1, max_length=300)
    duration_seconds: int | None = Field(default=None, ge=30, le=60)
    budget: float | None = Field(default=None, gt=0)
    characters: list[str] | None = Field(default=None, min_length=2, max_length=4)
    actor: str = Field(default="studio-editor", min_length=1, max_length=120)


class NarrativeEventCreateRequest(NarrativeEventInput):
    actor: str = Field(default="story-editor", min_length=1, max_length=120)


class NarrativeEventUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    chapter_number: int | None = Field(default=None, ge=1, le=100_000)
    sequence: int | None = Field(default=None, ge=1, le=10_000)
    title: str | None = Field(default=None, min_length=1, max_length=240)
    scene: str | None = Field(default=None, min_length=1, max_length=240)
    summary: str | None = Field(default=None, min_length=1, max_length=2_000)
    characters: list[str] | None = Field(default=None, min_length=1, max_length=4)
    importance: Literal["MAINLINE", "SUPPORTING", "TRANSITION"] | None = None
    emotions: list[str] | None = Field(default=None, max_length=6)
    estimated_duration_seconds: int | None = Field(default=None, ge=1, le=60)
    source_chapter_id: str | None = Field(default=None, max_length=120)
    source_locator: str | None = Field(default=None, max_length=500)
    source_excerpt: str | None = Field(default=None, max_length=8_000)
    expected_revision: int | None = Field(default=None, ge=1)
    actor: str = Field(default="story-editor", min_length=1, max_length=120)


class NarrativeEventReviewRequest(BaseModel):
    status: Literal["APPROVED", "CHANGES_REQUESTED"]
    comment: str = Field(default="", max_length=2_000)
    expected_revision: int | None = Field(default=None, ge=1)
    actor: str = Field(default="story-reviewer", min_length=1, max_length=120)


class AdaptationSceneCreateRequest(AdaptationSceneInput):
    actor: str = Field(default="script-editor", min_length=1, max_length=120)


class AdaptationSceneUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sequence: int | None = Field(default=None, ge=1, le=10_000)
    heading: str | None = Field(default=None, min_length=1, max_length=240)
    synopsis: str | None = Field(default=None, min_length=1, max_length=4_000)
    beats: list[str] | None = Field(default=None, max_length=12)
    dialogue_draft: str | None = Field(default=None, max_length=8_000)
    characters: list[str] | None = Field(default=None, min_length=1, max_length=4)
    mood: str | None = Field(default=None, max_length=120)
    estimated_duration_seconds: int | None = Field(default=None, ge=1, le=60)
    source_event_ids: list[str] | None = Field(default=None, min_length=1, max_length=12)
    expected_revision: int | None = Field(default=None, ge=1)
    actor: str = Field(default="script-editor", min_length=1, max_length=120)


class AdaptationSceneReviewRequest(BaseModel):
    status: Literal["APPROVED", "CHANGES_REQUESTED"]
    comment: str = Field(default="", max_length=2_000)
    expected_revision: int | None = Field(default=None, ge=1)
    actor: str = Field(default="script-reviewer", min_length=1, max_length=120)


class AdaptationSceneDeriveRequest(BaseModel):
    actor: str = Field(default="script-editor", min_length=1, max_length=120)


class SourceChapterCreateRequest(NarrativeSourceChapterInput):
    actor: str = Field(default="story-editor", min_length=1, max_length=120)


class SourceChapterUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_name: str | None = Field(default=None, min_length=1, max_length=240)
    rights_basis: str | None = Field(default=None, min_length=1, max_length=500)
    allow_external_processing: bool | None = None
    chapter_number: int | None = Field(default=None, ge=1, le=100_000)
    title: str | None = Field(default=None, min_length=1, max_length=240)
    content: str | None = Field(default=None, min_length=1, max_length=120_000)
    expected_revision: int | None = Field(default=None, ge=1)
    actor: str = Field(default="story-editor", min_length=1, max_length=120)


class SourceDocumentImportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=240)
    content_b64: str = Field(min_length=1, max_length=28_000_000)
    source_name: str = Field(min_length=1, max_length=240)
    rights_basis: str = Field(default="project-owned", min_length=1, max_length=500)
    allow_external_processing: bool = False
    chapter_number_start: int = Field(default=1, ge=1, le=100_000)
    actor: str = Field(default="story-editor", min_length=1, max_length=120)


class NarrativeCandidateExtractRequest(BaseModel):
    actor: str = Field(default="story-editor", min_length=1, max_length=120)


class NarrativeCandidateAdoptRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    characters: list[str] | None = Field(default=None, min_length=1, max_length=4)
    event_id: str | None = Field(
        default=None,
        min_length=1,
        max_length=120,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]*$",
    )
    expected_revision: int | None = Field(default=None, ge=1)
    actor: str = Field(default="story-editor", min_length=1, max_length=120)


class NarrativeCandidateDiscardRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str = Field(default="", max_length=500)
    expected_revision: int | None = Field(default=None, ge=1)
    actor: str = Field(default="story-editor", min_length=1, max_length=120)


class ShotUpdateRequest(BaseModel):
    scene: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, min_length=1, max_length=2000)
    characters: list[str] | None = Field(default=None, min_length=1, max_length=4)
    duration_seconds: int | None = Field(default=None, ge=1, le=5)
    mood: str | None = Field(default=None, min_length=1, max_length=120)
    subtitle_text: str | None = Field(default=None, max_length=500)
    actor: str = Field(default="studio-editor", min_length=1, max_length=120)


class PackageImportRequest(BaseModel):
    package_zip: str | None = Field(default=None, max_length=2000)
    package_zip_b64: str | None = Field(default=None)
    project_id: str | None = Field(
        default=None,
        min_length=1,
        max_length=64,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]*$",
    )
    actor: str = Field(default="studio-user", min_length=1, max_length=120)


class ReferenceAssetRequest(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    content_b64: str = Field(min_length=1, max_length=14_000_000)
    license: str = Field(min_length=1, max_length=120)
    kind: Literal[
        "character_reference",
        "style_reference",
        "location_reference",
    ] = "character_reference"
    character: str | None = Field(default=None, max_length=120)
    source: str = Field(default="studio-upload", min_length=1, max_length=120)
    actor: str = Field(default="studio-user", min_length=1, max_length=120)


class AudioTrackRequest(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    content_b64: str = Field(min_length=1, max_length=70_000_000)
    license: str = Field(
        default="user_supplied_or_project_owned",
        min_length=1,
        max_length=120,
    )
    source: str = Field(default="studio-upload", min_length=1, max_length=500)
    actor: str = Field(default="studio-user", min_length=1, max_length=120)


class VoiceoverRequest(BaseModel):
    text: str = Field(min_length=1, max_length=12000)
    voice: str = Field(default="default", min_length=1, max_length=120)
    language: str = Field(default="zh-CN", min_length=2, max_length=20)
    license: str = Field(default="unverified", min_length=1, max_length=120)
    source: str = Field(default="", max_length=500)
    actor: str = Field(default="studio-user", min_length=1, max_length=120)


class DialogueTimelineRequest(BaseModel):
    lines: list[DialogueLine] = Field(min_length=1, max_length=80)
    license: str = Field(default="unverified", min_length=1, max_length=120)
    source: str = Field(default="", max_length=500)
    actor: str = Field(default="studio-user", min_length=1, max_length=120)


class PlanningRequest(BaseModel):
    memory_project_ids: list[str] | None = Field(default=None, max_length=20)


class ArchiveImportRequest(BaseModel):
    archive_zip: str | None = Field(default=None, max_length=2000)
    archive_zip_b64: str | None = Field(default=None)
    project_id: str | None = Field(
        default=None,
        min_length=1,
        max_length=64,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]*$",
    )
    actor: str = Field(default="studio-user", min_length=1, max_length=120)


class ActorRequest(BaseModel):
    actor: str = Field(default="studio-user", min_length=1, max_length=120)


class ProductionStageReturnRequest(ActorRequest):
    reason: str = Field(default="", max_length=2_000)


class QueueDrainRequest(BaseModel):
    limit: int = Field(default=50, ge=1, le=500)
    actor: str = Field(default="generation-worker", min_length=1, max_length=120)


class QueueDrainAllRequest(BaseModel):
    limit: int = Field(default=50, ge=1, le=500)
    actor: str = Field(default="generation-worker", min_length=1, max_length=120)
    include_archived: bool = False


class WorkerRegisterRequest(BaseModel):
    worker_id: str = Field(min_length=1, max_length=120)
    capabilities: list[str] = Field(default_factory=list, max_length=32)
    resources: dict[str, Any] = Field(default_factory=dict)
    concurrency: int = Field(default=1, ge=1, le=64)


class WorkerHeartbeatRequest(BaseModel):
    job_ids: list[str] = Field(default_factory=list, max_length=64)
    resources: dict[str, Any] | None = None


class WorkerClaimRequest(BaseModel):
    limit: int = Field(default=1, ge=1, le=64)
    project_id: str | None = Field(default=None, max_length=64)
    minimum_gpu_memory_mib: int = Field(default=0, ge=0, le=1_048_576)
    provider_name: str | None = Field(default=None, min_length=1, max_length=120)


class StaleJobRecoveryRequest(BaseModel):
    stale_after_seconds: float | None = Field(default=None, ge=1, le=604800)
    actor: str = Field(default="operations", min_length=1, max_length=120)


class RetryScheduleRequest(BaseModel):
    delay_seconds: float | None = Field(default=None, ge=0, le=86400)
    actor: str = Field(default="generation-worker", min_length=1, max_length=120)


class VariantCompareRequest(BaseModel):
    candidate_count: int = Field(default=2, ge=1, le=4)
    actor: str = Field(default="studio-optimizer", min_length=1, max_length=120)


class VariantPromoteRequest(BaseModel):
    comment: str = Field(default="", max_length=2000)
    actor: str = Field(default="studio-reviewer", min_length=1, max_length=120)


class LicenseRegistryRequest(BaseModel):
    records: list[dict[str, Any]] = Field(min_length=1, max_length=500)
    source: str = Field(default="studio-import", min_length=1, max_length=500)
    actor: str = Field(default="studio-governance", min_length=1, max_length=120)


class LicenseRegistrySyncRequest(BaseModel):
    url: str | None = Field(default=None, max_length=2000)
    timeout_seconds: float = Field(default=10, gt=0, le=60)
    actor: str = Field(default="studio-governance", min_length=1, max_length=120)


class ProviderCallbackRequest(BaseModel):
    event_id: str = Field(min_length=1, max_length=160)
    provider: str = Field(min_length=1, max_length=120)
    status: Literal["RUNNING", "SUCCEEDED", "FAILED", "CANCELED"]
    reason: str | None = Field(default=None, max_length=2000)
    artifact_uri: str | None = Field(default=None, max_length=2000)
    artifact_kind: Literal["image", "video"] = "video"
    mime_type: str | None = Field(default=None, max_length=120)
    estimated_cost: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    actual_cost: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    actor: str = Field(default="provider-callback", min_length=1, max_length=120)
    worker_id: str | None = Field(default=None, min_length=1, max_length=120)
    external_reference: str | None = Field(default=None, min_length=1, max_length=240)


class ProviderCircuitRecoveryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    actor: str = Field(default="provider-operations", min_length=1, max_length=120)


class OperationsAlertAcknowledgeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    actor: str = Field(default="operations-operator", min_length=1, max_length=120)
    note: str = Field(default="", max_length=2000)


class ProjectMemberRequest(BaseModel):
    subject: str = Field(min_length=1, max_length=160)
    role: Literal["owner", "viewer", "editor", "reviewer", "publisher"] = "viewer"


class ProjectCommentRequest(BaseModel):
    body: str = Field(min_length=1, max_length=2000)
    shot_id: str | None = Field(default=None, max_length=160)


class CollaborationPresenceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["ACTIVE", "AWAY", "BUSY"] = "ACTIVE"
    section: str | None = Field(default=None, max_length=160)
    ttl_seconds: int = Field(default=90, ge=15, le=600)


class EditLockRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target_type: Literal[
        "brief",
        "story_bible",
        "narrative_event",
        "scene",
        "shot",
        "timeline",
        "prompt",
    ]
    target_id: str = Field(min_length=1, max_length=240)
    ttl_seconds: int = Field(default=120, ge=15, le=900)


class CollaborationDocumentOperationsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    operations: list[dict[str, Any]] = Field(min_length=1, max_length=1_000)


class CollaborationDocumentReplaceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str = Field(default="", max_length=30_000)


class ContentCredentialRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    asset_id: str = Field(min_length=1, max_length=240)
    actor: str = Field(default="studio-governance", min_length=1, max_length=120)


class PromptVersionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str = Field(min_length=1, max_length=120)
    template: str = Field(min_length=1, max_length=16_000)
    label: str = Field(default="", max_length=240)
    activate: bool = False
    actor: str = Field(default="studio-user", min_length=1, max_length=120)


class PromptActivateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    actor: str = Field(default="studio-user", min_length=1, max_length=120)


class EvaluationAnnotationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target_type: Literal["project", "shot", "asset", "prompt"]
    target_id: str = Field(min_length=1, max_length=240)
    verdict: Literal["PASS", "FAIL", "NEEDS_CHANGES", "UNSURE"]
    note: str = Field(default="", max_length=4_000)
    rating: float | None = Field(default=None, ge=0, le=1)
    actor: str = Field(default="studio-reviewer", min_length=1, max_length=120)


class EvaluationBaselineRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=240)
    minimum_score: float | None = Field(default=None, ge=0, le=1)
    actor: str = Field(default="quality-owner", min_length=1, max_length=120)


class PromptExperimentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=240)
    prompt_key: str = Field(min_length=1, max_length=120)
    control_prompt_id: str = Field(min_length=1, max_length=120)
    treatment_prompt_id: str = Field(min_length=1, max_length=120)
    objective: str = Field(default="", max_length=2_000)
    actor: str = Field(default="studio-user", min_length=1, max_length=120)


class AssetDerivativeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    actor: str = Field(default="studio-user", min_length=1, max_length=120)


class TimelineUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    clips: list[dict[str, Any]] = Field(min_length=1, max_length=500)
    actor: str = Field(default="studio-editor", min_length=1, max_length=120)


class TimelineExportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    format: Literal["otio_json", "edl_csv"] = "otio_json"
    actor: str = Field(default="studio-editor", min_length=1, max_length=120)


@asynccontextmanager
async def app_lifespan(app: FastAPI):
    scheduler = app.state.registry_sync_scheduler
    task = None
    if scheduler["enabled"]:
        async def run_registry_sync() -> None:
            scheduler["running"] = True
            try:
                while True:
                    await asyncio.sleep(scheduler["interval_seconds"])
                    try:
                        if not app.state.mediaforge.control_plane_status(
                            acquire=True
                        ).get("ready_for_traffic"):
                            continue
                        await asyncio.to_thread(
                            app.state.mediaforge.sync_license_registry,
                            actor="license-registry-scheduler",
                        )
                        scheduler["last_error"] = None
                    except Exception as exc:  # pragma: no cover - scheduler boundary
                        scheduler["last_error"] = str(exc)
            except asyncio.CancelledError:
                raise
            finally:
                scheduler["running"] = False

        task = asyncio.create_task(run_registry_sync())
        app.state.registry_sync_task = task
    try:
        yield
    finally:
        if task is not None:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        app.state.mediaforge.release_control_plane()


def create_app(output_root: Path | None = None) -> FastAPI:
    provider_bundles = build_provider_bundles_from_env()
    provider_bundle = next(
        (bundle for bundle in provider_bundles if bundle.configured),
        provider_bundles[0],
    )
    has_declared_real_provider = any(
        bundle.mode not in {"mock", "unknown", ""}
        for bundle in provider_bundles
    )
    callback_security = CallbackSecurity.from_env(
        provider_mode=("production" if has_declared_real_provider else provider_bundle.mode)
    )
    replicate_webhook_security = ReplicateWebhookSecurity.from_env()
    settlement_callback_security = SettlementCallbackSecurity.from_env()
    stripe_settlement_webhook = StripeSettlementWebhook.from_env()
    auth_manager = AuthManager.from_env()
    metrics_access = MetricsAccessPolicy.from_env()
    try:
        registry_sync_interval = float(
            os.getenv("MEDIAFORGE_LICENSE_REGISTRY_SYNC_INTERVAL_SECONDS", "0")
        )
    except ValueError as exc:
        raise ValueError(
            "MEDIAFORGE_LICENSE_REGISTRY_SYNC_INTERVAL_SECONDS must be a number"
        ) from exc
    if registry_sync_interval < 0 or (0 < registry_sync_interval < 30):
        raise ValueError(
            "MEDIAFORGE_LICENSE_REGISTRY_SYNC_INTERVAL_SECONDS must be 0 or at least 30"
        )
    story_planner = build_story_planner_from_env()
    service = MediaForgeService(
        output_root
        or Path(os.getenv("MEDIAFORGE_ARTIFACT_ROOT", "artifacts/api")),
        provider=provider_bundle.provider,
        story_planner=story_planner,
        provider_registrations=[
            ProviderRegistration(
                provider=bundle.provider,
                priority=len(provider_bundles) - index,
                enabled=bundle.configured,
            )
            for index, bundle in enumerate(provider_bundles)
        ],
    )
    service.set_provider_status(
        provider_bundle.status_view(),
        statuses=[bundle.status_view() for bundle in provider_bundles],
    )
    app = FastAPI(
        title="MediaForge P0 API",
        version="0.1.0",
        description="Complete short-drama production loop.",
        lifespan=app_lifespan,
    )
    app.state.mediaforge = service
    app.state.callback_security = callback_security
    app.state.replicate_webhook_security = replicate_webhook_security
    app.state.settlement_callback_security = settlement_callback_security
    app.state.stripe_settlement_webhook = stripe_settlement_webhook
    app.state.auth_manager = auth_manager
    app.state.http_metrics = HttpMetrics()
    app.state.metrics_access = metrics_access
    app.state.registry_sync_scheduler = {
        "enabled": bool(
            registry_sync_interval
            and os.getenv("MEDIAFORGE_LICENSE_REGISTRY_SYNC_URL", "").strip()
        ),
        "interval_seconds": registry_sync_interval,
        "running": False,
        "last_error": None,
    }

    @app.exception_handler(QuotaViolation)
    async def quota_violation_handler(_request: Request, exc: QuotaViolation):
        return JSONResponse(
            status_code=429,
            content={"detail": str(exc), "quota": exc.report},
            headers={"Retry-After": "60"},
        )

    @app.middleware("http")
    async def prevent_stale_studio_assets(request: Request, call_next):
        response = await call_next(request)
        path = request.url.path.lower()
        if path == "/" or path.endswith((".html", ".css", ".js")):
            response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
            response.headers["Pragma"] = "no-cache"
            response.headers["Expires"] = "0"
        return response

    def is_public_path(path: str, method: str) -> bool:
        if method.upper() not in {"GET", "HEAD"}:
            return False
        return (
            path in {"/", "/health", "/livez", "/auth/status", "/auth/login", "/auth/callback", "/auth/logout"}
            or (
                path in {"/metrics", "/metrics/runtime"}
                and not metrics_access.protected
            )
            or path.startswith(("/docs", "/redoc", "/openapi.json"))
            or path.startswith("/providers/")
            or path.startswith("/llm/")
            or path in {"/index.html", "/styles.css", "/app.js", "/favicon.ico"}
        )

    def is_settlement_callback_path(path: str, method: str) -> bool:
        return path == "/billing/settlements/callback" and method.upper() == "POST"

    def is_stripe_settlement_callback_path(path: str, method: str) -> bool:
        return path == "/billing/settlements/stripe" and method.upper() == "POST"

    def is_replicate_webhook_path(path: str, method: str) -> bool:
        return path == "/providers/replicate/webhook" and method.upper() == "POST"

    def project_id_from_path(path: str) -> str | None:
        parts = [part for part in path.split("/") if part]
        if len(parts) < 2 or parts[0] != "projects":
            return None
        if parts[1] in {"import", "import-package", "import-archive"}:
            return None
        return parts[1]

    @app.middleware("http")
    async def enforce_api_security(request: Request, call_next):
        started_at = perf_counter()
        response = None
        try:
            authorization = request.headers.get("Authorization")
            session_id = request.cookies.get("mediaforge_session")
            if (
                request.url.path in {"/metrics", "/metrics/runtime"}
                and request.method.upper() in {"GET", "HEAD"}
                and metrics_access.protected
            ):
                if not metrics_access.permits(authorization):
                    raise AuthenticationError("metrics bearer token is required")
                principal = Principal(
                    subject="metrics-collector",
                    role="viewer",
                    authenticated=True,
                )
            elif is_public_path(request.url.path, request.method):
                if authorization or session_id:
                    principal = auth_manager.authenticate(authorization, session_id=session_id)
                else:
                    principal = Principal(
                        subject="anonymous",
                        role="viewer",
                        authenticated=False,
                    )
            elif is_settlement_callback_path(request.url.path, request.method):
                settlement_callback_security.verify(
                    request.headers,
                    await request.body(),
                )
                principal = Principal(
                    subject="settlement-callback",
                    role="provider",
                    authenticated=True,
                )
            elif is_stripe_settlement_callback_path(request.url.path, request.method):
                request.state.stripe_settlement = stripe_settlement_webhook.verify_and_normalize(
                    request.headers,
                    await request.body(),
                )
                principal = Principal(
                    subject="stripe-settlement-webhook",
                    role="provider",
                    authenticated=True,
                )
            elif is_replicate_webhook_path(request.url.path, request.method):
                request.state.replicate_webhook_authentication = (
                    replicate_webhook_security.verify(
                        request.headers,
                        await request.body(),
                    )
                )
                principal = Principal(
                    subject="replicate-webhook",
                    role="provider",
                    authenticated=True,
                )
            else:
                principal = auth_manager.authenticate(authorization, session_id=session_id)
                auth_manager.authorize(
                    principal,
                    method=request.method,
                    path=request.url.path,
                )
            request.state.principal = principal
            if (
                request.url.path not in {"/health", "/livez"}
                and service.enterprise.control_plane.enabled
                and not service.control_plane_status(acquire=True).get(
                    "ready_for_traffic"
                )
            ):
                raise ControlPlaneUnavailable(
                    "this control plane is standby; retry through a healthy primary instance"
                )
            project_id = project_id_from_path(request.url.path)
            if principal.tenant_id and project_id:
                project = service.projects.get(project_id)
                if project and project.brief.tenant_id != principal.tenant_id:
                    raise AuthenticationError("project not found", status_code=404)
            worker_process = principal.role == "provider" and auth_manager.is_worker_process(method=request.method, path=request.url.path)
            if worker_process:
                worker_id = request.query_params.get("worker_id")
                worker = service.workers.get(worker_id) if worker_id else None
                if worker is None or (principal.tenant_id and worker.get("tenant_id") != principal.tenant_id):
                    raise AuthenticationError("a Worker registered for this tenant is required", status_code=403)
                # process_job also verifies job ownership, current lease and lease expiry.
            if project_id and principal.role != "admin" and not worker_process:
                project = service.projects.get(project_id)
                required_role = service.required_project_role(
                    method=request.method,
                    path=request.url.path,
                )
                if project and required_role:
                    project_role = service.project_member_role(
                        project_id,
                        principal.subject,
                    )
                    role_levels = service.project_role_levels()
                    if role_levels.get(project_role or "", 0) < role_levels[required_role]:
                        raise AuthenticationError(
                            "project membership does not allow this operation",
                            status_code=403,
                        )
            read_only_request = (
                request.method in {"GET", "HEAD"}
                and request.url.path not in {"/health", "/livez"}
            )
            limiter = (
                service.enterprise.read_rate_limiter
                if read_only_request
                else service.enterprise.rate_limiter
            )
            rate_key = (
                f"tenant:{principal.tenant_id}" if principal.tenant_id
                else f"subject:{principal.subject}" if principal.authenticated
                else f"ip:{request.client.host if request.client else 'anonymous'}"
            )
            rate_key = f"{rate_key}:{'read' if read_only_request else 'write'}"
            rate_limit = limiter.check(rate_key)
            if not rate_limit.allowed:
                response = JSONResponse(
                    status_code=429,
                    content={
                        "detail": "request rate limit exceeded",
                        "rate_limit": {
                            "limit": limiter.limit,
                            "remaining": rate_limit.remaining,
                            "retry_after_seconds": rate_limit.retry_after_seconds,
                            "reset_at": rate_limit.reset_at_epoch,
                        },
                    },
                    headers={
                        "Retry-After": str(rate_limit.retry_after_seconds or 1),
                        "X-RateLimit-Limit": str(limiter.limit),
                        "X-RateLimit-Remaining": "0",
                        "X-RateLimit-Reset": str(rate_limit.reset_at_epoch or 0),
                    },
                )
            else:
                def needs_planning_guard() -> bool:
                    return (
                        service.planning.status_view()['configured']
                        and project_id in service.projects
                        and request.method in {'POST', 'PUT', 'PATCH', 'DELETE'}
                        and not any(job.spec.project_id == project_id for job in service.jobs.all())
                        and request.url.path.strip('/').split('/')[2:3] != ['planning']
                    )
                control_plane_write = (
                    request.method in {'POST', 'PUT', 'PATCH', 'DELETE'}
                    and request.url.path != '/auth/logout'
                )
                if control_plane_write:
                    with service.control_plane_mutation_guard():
                        if needs_planning_guard():
                            with service.planning.mutation_guard(project_id):
                                response = await call_next(request)
                        else:
                            response = await call_next(request)
                elif needs_planning_guard():
                    with service.planning.mutation_guard(project_id):
                        response = await call_next(request)
                else:
                    response = await call_next(request)
                response.headers["X-RateLimit-Limit"] = str(limiter.limit)
                response.headers["X-RateLimit-Remaining"] = str(rate_limit.remaining)
                if rate_limit.reset_at_epoch is not None:
                    response.headers["X-RateLimit-Reset"] = str(
                        rate_limit.reset_at_epoch
                    )
        except PlanningBusy as exc:
            response = JSONResponse(status_code=409, content={'detail': str(exc)})
        except ControlPlaneUnavailable as exc:
            response = JSONResponse(status_code=503, content={'detail': str(exc)})
        except (
            AuthenticationError,
            SettlementCallbackError,
            ReplicateWebhookSecurityError,
        ) as exc:
            response = JSONResponse(
                status_code=exc.status_code,
                content={"detail": str(exc)},
                headers=(
                    {"WWW-Authenticate": "Bearer"}
                    if exc.status_code == 401
                    else None
                ),
            )
        except Exception:
            raise
        finally:
            route = request.scope.get("route")
            route_path = getattr(route, "path", request.url.path)
            status_code = response.status_code if response is not None else 500
            app.state.http_metrics.observe(
                method=request.method,
                route=str(route_path),
                status_code=status_code,
                duration=perf_counter() - started_at,
            )
        return response

    @app.get("/health")
    def health(response: Response) -> dict[str, Any]:
        control_plane = service.control_plane_status(acquire=True)
        if not control_plane.get("ready_for_traffic"):
            response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return {
            "status": "ok" if control_plane.get("ready_for_traffic") else "standby",
            "service": "mediaforge-studio",
            "version": "0.1.0",
            "storage": service.storage_status(),
            "control_plane": control_plane,
        }

    @app.get("/livez")
    def liveness() -> dict[str, str]:
        """Process-level liveness that never acquires a control-plane lease."""
        return {"status": "ok", "service": "mediaforge-studio"}

    @app.get("/metrics", response_class=PlainTextResponse)
    def metrics() -> str:
        return (
            app.state.http_metrics.prometheus()
            + service.runtime_metrics.prometheus()
            + service.provider_circuits_prometheus()
            + service.operations_alerts_prometheus()
        )

    @app.get("/metrics/runtime")
    def runtime_metrics() -> dict[str, Any]:
        return service.runtime_metrics_view()

    @app.get("/ops/alerts")
    def operations_alerts() -> dict[str, Any]:
        return service.operations_alerts()

    @app.post("/ops/alerts/{alert_code}/acknowledge")
    def acknowledge_operations_alert(
        alert_code: str,
        payload: OperationsAlertAcknowledgeRequest,
        request: Request,
    ) -> dict[str, Any]:
        try:
            principal = request.state.principal
            actor = principal.subject if principal.authenticated else payload.actor
            return service.acknowledge_operations_alert(
                alert_code,
                actor=actor,
                note=payload.note,
            )
        except WorkflowError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.get("/llm/status")
    def llm_status() -> dict[str, Any]:
        return service.planner_status()

    @app.get("/webhooks/status")
    def webhook_status() -> dict[str, Any]:
        return service.webhook_status()

    @app.get("/delivery/status")
    def delivery_status() -> dict[str, Any]:
        return service.delivery_dispatch_status()

    @app.get("/quality/status")
    def quality_status() -> dict[str, Any]:
        return service.quality_evaluation_status()

    @app.get("/speech/status")
    def speech_status() -> dict[str, Any]:
        return service.speech_synthesizer.status_view()

    @app.get("/lipsync/status")
    def lipsync_status() -> dict[str, Any]:
        return service.lipsync_status()

    @app.get("/source-ingest/status")
    def source_ingest_status() -> dict[str, Any]:
        return service.source_ingest_status()

    @app.get("/enterprise/status")
    def enterprise_status() -> dict[str, Any]:
        return service.enterprise_status()

    @app.get("/projects/{project_id}/memory")
    def search_project_memory(
        project_id: str, request: Request, query: str = Query(min_length=1, max_length=500),
        limit: int | None = Query(default=None, ge=1, le=50),
        source_project_ids: list[str] | None = Query(default=None, max_length=20),
    ) -> dict[str, Any]:
        try:
            principal = request.state.principal
            return service.story_memory_search(project_id, query, limit, source_project_ids=source_project_ids,
                                               subject=None if principal.role == "admin" else principal.subject)
        except ProjectNotFound as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except MemoryUnavailable as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc

    @app.get("/projects/{project_id}/memory/sources")
    def project_memory_sources(project_id: str, request: Request) -> dict[str, Any]:
        try:
            principal = request.state.principal
            return service.story_memory_sources(project_id, subject=None if principal.role == "admin" else principal.subject)
        except ProjectNotFound as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post("/projects/{project_id}/memory/ragflow-sync")
    def sync_project_memory_to_ragflow(
        project_id: str,
        payload: RAGFlowWriteSyncRequest,
        request: Request,
    ) -> dict[str, Any]:
        try:
            return service.sync_story_memory_to_ragflow(
                project_id,
                actor=request.state.principal.subject,
            )
        except ProjectNotFound as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except MemoryUnavailable as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        except WorkflowError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.post("/enterprise/probe")
    def enterprise_probe() -> dict[str, Any]:
        return service.enterprise.probe()

    @app.post("/enterprise/memory/probe")
    def memory_probe() -> dict[str, Any]:
        return service.story_memory.probe()

    @app.post('/planning/probe')
    def planning_probe() -> dict[str, Any]:
        return service.planning.probe()

    @app.get("/billing/summary")
    def billing_summary(request: Request) -> dict[str, Any]:
        return service.enterprise.billing.summary(request.state.principal.tenant_id)

    @app.get("/billing/events")
    def billing_events(request: Request, project_id: str | None = Query(default=None, max_length=120),
                       category: str | None = Query(default=None, max_length=120),
                       currency: str | None = Query(default=None, pattern="^[A-Za-z]{3}$"),
                       since: float | None = Query(default=None), until: float | None = Query(default=None),
                       limit: int = Query(default=50, ge=1, le=500), offset: int = Query(default=0, ge=0)) -> dict[str, Any]:
        tenant_id = request.state.principal.tenant_id
        try:
            page = service.enterprise.billing.event_page(tenant_id, project_id=project_id, category=category,
                                                        currency=currency, since=since, until=until, limit=limit, offset=offset)
        except EnterpriseConfigurationError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return {
            "schema_version": "mediaforge-billing-events-v1",
            "tenant_id": tenant_id or "all",
            **page,
        }

    @app.post("/billing/events")
    def billing_event(payload: BillingEventRequest, request: Request) -> dict[str, Any]:
        principal = request.state.principal
        tenant_id = principal.tenant_id or "default"
        if payload.project_id:
            project = service.projects.get(payload.project_id)
            if project is None or (principal.tenant_id and project.brief.tenant_id != principal.tenant_id):
                raise HTTPException(status_code=404, detail="project not found")
            tenant_id = project.brief.tenant_id
        try:
            event = service.enterprise.billing.record(
                **payload.model_dump(), tenant_id=tenant_id,
            )
        except EnterpriseConfigurationError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return {"event": event, "summary": service.enterprise.billing.summary(principal.tenant_id)}

    @app.get("/billing/settlements/summary")
    def billing_settlement_summary(request: Request) -> dict[str, Any]:
        return service.enterprise.billing.settlement_summary(request.state.principal.tenant_id)

    @app.get("/billing/settlements")
    def billing_settlements(
        request: Request,
        settlement_status: str | None = Query(default=None, alias="status", max_length=20),
        currency: str | None = Query(default=None, pattern="^[A-Za-z]{3}$"),
        limit: int = Query(default=50, ge=1, le=500),
        offset: int = Query(default=0, ge=0),
    ) -> dict[str, Any]:
        try:
            page = service.enterprise.billing.settlement_page(
                request.state.principal.tenant_id,
                status=settlement_status,
                currency=currency,
                limit=limit,
                offset=offset,
            )
        except EnterpriseConfigurationError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return {"schema_version": "mediaforge-settlements-v1", **page}

    @app.post("/billing/settlements")
    def billing_settlement(payload: BillingSettlementRequest, request: Request) -> dict[str, Any]:
        principal = request.state.principal
        try:
            settlement = service.enterprise.billing.record_settlement(
                **payload.model_dump(),
                tenant_id=principal.tenant_id or "default",
            )
        except EnterpriseConfigurationError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return {
            "settlement": settlement,
            "summary": service.enterprise.billing.settlement_summary(principal.tenant_id),
        }

    @app.get("/billing/settlements/callback/status")
    def billing_settlement_callback_status() -> dict[str, Any]:
        return settlement_callback_security.status_view()

    @app.get("/billing/settlements/stripe/status")
    def stripe_settlement_status() -> dict[str, Any]:
        return stripe_settlement_webhook.status_view()

    @app.post("/billing/settlements/stripe")
    def stripe_settlement_webhook_callback(request: Request) -> dict[str, Any]:
        try:
            settlement = service.enterprise.billing.record_settlement(
                **request.state.stripe_settlement,
            )
        except EnterpriseConfigurationError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return {
            "settlement": settlement,
            "summary": service.enterprise.billing.settlement_summary(settlement["tenant_id"]),
        }

    @app.post("/billing/settlements/callback")
    def billing_settlement_callback(payload: BillingSettlementCallbackRequest) -> dict[str, Any]:
        if not settlement_callback_security.permits_provider(payload.provider):
            raise HTTPException(status_code=422, detail="settlement provider is not allowed for callbacks")
        try:
            settlement = service.enterprise.billing.record_settlement(**payload.model_dump())
        except EnterpriseConfigurationError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return {
            "settlement": settlement,
            "summary": service.enterprise.billing.settlement_summary(payload.tenant_id),
        }

    @app.get("/workers")
    def workers(request: Request) -> dict[str, Any]:
        return service.worker_status(tenant_id=request.state.principal.tenant_id)

    @app.post("/workers/register")
    def register_worker(worker: WorkerRegisterRequest, request: Request) -> dict[str, Any]:
        try:
            return service.register_worker(
                worker.worker_id,
                capabilities=worker.capabilities,
                resources=worker.resources,
                concurrency=worker.concurrency,
                tenant_id=request.state.principal.tenant_id,
            )
        except WorkflowError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.post("/workers/{worker_id}/heartbeat")
    def worker_heartbeat(
        worker_id: str,
        heartbeat: WorkerHeartbeatRequest,
        request: Request,
    ) -> dict[str, Any]:
        try:
            return service.worker_heartbeat(
                worker_id,
                job_ids=heartbeat.job_ids,
                resources=heartbeat.resources,
                tenant_id=request.state.principal.tenant_id,
            )
        except WorkflowError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.post("/workers/{worker_id}/claim")
    def claim_worker_jobs(
        worker_id: str,
        claim: WorkerClaimRequest,
        request: Request,
    ) -> dict[str, Any]:
        try:
            return service.claim_worker_jobs(
                worker_id,
                limit=claim.limit,
                project_id=claim.project_id,
                minimum_gpu_memory_mib=claim.minimum_gpu_memory_mib,
                provider_name=claim.provider_name,
                tenant_id=request.state.principal.tenant_id,
            )
        except WorkflowError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.get("/auth/status")
    def auth_status() -> dict[str, Any]:
        return auth_manager.status_view()

    @app.get("/auth/login")
    def auth_login() -> RedirectResponse:
        return RedirectResponse(auth_manager.begin_browser_login(), status_code=303)

    @app.get("/auth/callback")
    def auth_callback(
        code: str | None = Query(default=None, max_length=4096),
        state: str | None = Query(default=None, max_length=512),
        error: str | None = Query(default=None, max_length=120),
    ) -> RedirectResponse:
        if error:
            raise AuthenticationError(f"OIDC login failed: {error}")
        if not code or not state:
            raise AuthenticationError("OIDC callback requires code and state")
        try:
            session_id, _principal, ttl = auth_manager.complete_browser_login(code=code, state=state)
        except AuthenticationError as exc:
            raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
        response = RedirectResponse("/", status_code=303)
        secure = os.getenv("MEDIAFORGE_OIDC_COOKIE_SECURE", "false").strip().lower() in {"1", "true", "yes", "on"}
        response.set_cookie(
            "mediaforge_session",
            session_id,
            max_age=ttl,
            httponly=True,
            secure=secure,
            samesite="lax",
            path="/",
        )
        return response

    @app.post("/auth/logout")
    def auth_logout(request: Request) -> JSONResponse:
        auth_manager.logout_browser_session(request.cookies.get("mediaforge_session"))
        response = JSONResponse({"logged_out": True})
        response.delete_cookie("mediaforge_session", path="/")
        return response

    @app.get("/auth/me")
    def auth_me(request: Request) -> dict[str, Any]:
        return request.state.principal.as_dict()

    @app.get("/tenants/me/quota")
    def tenant_quota(request: Request) -> dict[str, Any]:
        principal = request.state.principal
        return service.tenant_quota(principal.tenant_id)

    @app.get("/tenants/quota/status")
    def quota_status() -> dict[str, Any]:
        return service.quota_status()

    @app.get("/tenants/me/cost")
    def tenant_cost(
        include_archived: bool = Query(default=False),
        request: Request = None,
    ) -> dict[str, Any]:
        principal = getattr(request.state, "principal", None)
        return service.tenant_cost_report(
            principal.tenant_id if principal else None,
            include_archived=include_archived,
        )

    @app.get("/tenants/me/cost/export")
    def export_tenant_cost(
        file_format: Literal["json", "csv"] = Query(default="json", alias="format"),
        include_archived: bool = Query(default=False),
        request: Request = None,
    ) -> FileResponse:
        principal = getattr(request.state, "principal", None)
        path = service.export_tenant_cost_report(
            principal.tenant_id if principal else None,
            file_format=file_format,
            include_archived=include_archived,
        )
        media_type = "application/json" if file_format == "json" else "text/csv"
        return FileResponse(path, media_type=media_type, filename=path.name)

    @app.get("/providers/status")
    def provider_status() -> dict:
        return service.provider_status_view()

    @app.get("/providers/health")
    def provider_health() -> dict:
        return service.provider_health()

    @app.get("/providers/circuits")
    def provider_circuits() -> dict:
        return service.provider_circuit_status()

    @app.get("/providers/operations")
    def provider_operations(
        limit: int = Query(default=50, ge=1, le=200),
    ) -> dict[str, Any]:
        return service.provider_operations_view(limit=limit)

    @app.post("/providers/{provider_name}/circuit/recover")
    def recover_provider_circuit(
        provider_name: str,
        payload: ProviderCircuitRecoveryRequest,
        request: Request,
    ) -> dict[str, Any]:
        try:
            principal = request.state.principal
            actor = (
                principal.subject
                if principal.authenticated
                else payload.actor
            )
            return service.recover_provider_circuit(
                provider_name,
                actor=actor,
            )
        except WorkflowError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.post("/providers/warmup")
    def provider_warmup() -> dict[str, Any]:
        return service.provider_warmup()

    @app.get("/providers/diagnostics")
    def provider_diagnostics() -> dict:
        diagnostics = service.provider_diagnostics()
        callback_status = callback_security.status_view()
        provider_statuses = diagnostics["status"].get("providers") or [
            diagnostics["status"]
        ]
        production_mode = any(
            item.get("mode") not in {"mock", "unknown", ""}
            for item in provider_statuses
        )
        callback_ready = bool(callback_status["configured"])
        replicate_webhook_status = replicate_webhook_security.status_view()
        replicate_webhook_enabled = any(
            bool((item.get("details") or {}).get("webhook_url_template_configured"))
            for item in provider_statuses
            if item.get("mode") == "replicate"
        )
        diagnostics["callback_security"] = callback_status
        diagnostics["replicate_webhook_security"] = replicate_webhook_status
        diagnostics["checks"].append(
            {
                "code": "callback_authentication",
                "passed": callback_ready,
                "blocking": production_mode,
                "message": (
                    "Provider callbacks require a valid HMAC-SHA256 signature."
                    if callback_ready
                    else (
                        "Configure MEDIAFORGE_CALLBACK_SECRET before using production callbacks."
                        if production_mode
                        else "Unsigned callbacks are allowed only in local Mock mode."
                    )
                ),
            }
        )
        if production_mode and not callback_ready:
            diagnostics["production_ready"] = False
            if diagnostics["grade"] == "READY":
                diagnostics["grade"] = "BLOCKED"
            diagnostics["next_actions"].append(
                {
                    "code": "CONFIGURE_CALLBACK_SECRET",
                    "priority": "blocking",
                    "message": "Set MEDIAFORGE_CALLBACK_SECRET and restart the service.",
                }
            )
        if replicate_webhook_enabled:
            webhook_ready = bool(replicate_webhook_status["configured"])
            diagnostics["checks"].append(
                {
                    "code": "replicate_webhook_authentication",
                    "passed": webhook_ready,
                    "blocking": True,
                    "message": (
                        "Replicate native webhook verification is configured."
                        if webhook_ready
                        else (
                            "Configure REPLICATE_WEBHOOK_SIGNING_SECRET before "
                            "using replicate-webhook execution."
                        )
                    ),
                }
            )
            if not webhook_ready:
                diagnostics["production_ready"] = False
                if diagnostics["grade"] == "READY":
                    diagnostics["grade"] = "BLOCKED"
                diagnostics["next_actions"].append(
                    {
                        "code": "CONFIGURE_REPLICATE_WEBHOOK_SECRET",
                        "priority": "blocking",
                        "message": (
                            "Set REPLICATE_WEBHOOK_SIGNING_SECRET and restart "
                            "the API before using replicate-webhook execution."
                        ),
                    }
                )
        return diagnostics

    @app.get("/providers/contracts")
    def provider_contracts() -> dict[str, Any]:
        return service.provider_contract_report()

    @app.post("/projects/{project_id}/providers/contracts/validate")
    def validate_project_provider_contracts(
        project_id: str,
        request: ActorRequest,
    ) -> dict[str, Any]:
        try:
            return service.validate_provider_contracts(
                project_id,
                actor=request.actor,
            )
        except ProjectNotFound as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except WorkflowError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.get("/providers/callback-security")
    def provider_callback_security() -> dict:
        return callback_security.status_view()

    @app.get("/providers/replicate/webhook-security")
    def replicate_webhook_security_status() -> dict:
        return replicate_webhook_security.status_view()

    @app.get("/ops/readiness")
    def production_readiness() -> dict[str, Any]:
        return service.production_readiness(
            callback_configured=bool(callback_security.status_view()["configured"])
        )

    @app.get("/governance/license-registry")
    def license_registry() -> dict:
        view = service.license_registry_view()
        view["scheduler"] = dict(app.state.registry_sync_scheduler)
        return view

    @app.get("/governance/license-registry/sync/status")
    def license_registry_sync_status() -> dict:
        return {
            "scheduler": dict(app.state.registry_sync_scheduler),
            "sync": (
                dict(service.license_registry_metadata["sync"])
                if isinstance(service.license_registry_metadata.get("sync"), dict)
                else None
            ),
        }

    @app.post("/governance/license-registry/validate")
    def validate_license_registry(request: LicenseRegistryRequest) -> dict:
        try:
            return service.validate_license_registry(
                request.records,
                source=request.source,
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.post("/governance/license-registry/import")
    def import_license_registry(request: LicenseRegistryRequest) -> dict:
        try:
            return service.import_license_registry(
                request.records,
                source=request.source,
                actor=request.actor,
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.post("/governance/license-registry/sync")
    def sync_license_registry(request: LicenseRegistrySyncRequest) -> dict:
        try:
            return service.sync_license_registry(
                url=request.url,
                timeout_seconds=request.timeout_seconds,
                actor=request.actor,
            )
        except RegistrySyncError as exc:
            raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc

    @app.get("/governance/license-registry/export")
    def export_license_registry() -> FileResponse:
        path = service.export_license_registry()
        return FileResponse(
            path,
            media_type="application/json",
            filename=path.name,
        )

    @app.get("/studio/overview")
    def studio_overview(
        include_archived: bool = Query(default=False),
        request: Request = None,
    ) -> dict:
        principal = getattr(request.state, "principal", None)
        return service.studio_overview(
            include_archived=include_archived,
            tenant_id=principal.tenant_id if principal else None,
        )

    @app.get("/studio/metrics")
    def studio_metrics(
        include_archived: bool = Query(default=False),
        request: Request = None,
    ) -> dict:
        principal = getattr(request.state, "principal", None)
        return service.studio_metrics(
            include_archived=include_archived,
            tenant_id=principal.tenant_id if principal else None,
        )

    @app.get("/studio/metrics/export")
    def export_studio_metrics(
        file_format: Literal["json", "csv"] = Query(default="json", alias="format"),
        include_archived: bool = Query(default=False),
        request: Request = None,
    ) -> FileResponse:
        path = service.export_studio_metrics(
            file_format=file_format,
            include_archived=include_archived,
            tenant_id=(
                request.state.principal.tenant_id
                if request and getattr(request.state, "principal", None)
                else None
            ),
        )
        media_type = "application/json" if file_format == "json" else "text/csv"
        return FileResponse(path, media_type=media_type, filename=path.name)

    @app.get("/projects")
    def list_projects(
        include_archived: bool = Query(default=False),
        request: Request = None,
    ) -> dict:
        principal = getattr(request.state, "principal", None)
        return service.list_projects(
            include_archived=include_archived,
            tenant_id=principal.tenant_id if principal else None,
        )

    @app.post("/projects", status_code=status.HTTP_201_CREATED)
    def create_project(brief: CreativeBrief, request: Request) -> dict:
        try:
            principal = request.state.principal
            if principal.tenant_id:
                if brief.tenant_id == "default":
                    brief = brief.model_copy(update={"tenant_id": principal.tenant_id})
                elif brief.tenant_id != principal.tenant_id:
                    raise HTTPException(status_code=403, detail="tenant mismatch")
            return service.create_project(
                brief,
                owner_subject=principal.subject,
            )
        except PolicyViolation as exc:
            raise HTTPException(
                status_code=422,
                detail={"message": str(exc), "report": exc.report},
            ) from exc
        except WorkflowError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post("/projects/import", status_code=status.HTTP_201_CREATED)
    def import_project(request: ProjectImportRequest, http_request: Request) -> dict:
        try:
            return service.import_project_snapshot(
                request.snapshot,
                target_project_id=request.project_id,
                actor=request.actor,
                tenant_id=http_request.state.principal.tenant_id,
            )
        except PolicyViolation as exc:
            raise HTTPException(
                status_code=422,
                detail={"message": str(exc), "report": exc.report},
            ) from exc
        except TenantViolation as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except WorkflowError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.post("/projects/import-package", status_code=status.HTTP_201_CREATED)
    def import_delivery_package(request: PackageImportRequest, http_request: Request) -> dict:
        try:
            return service.import_delivery_package(
                package_path=request.package_zip,
                package_b64=request.package_zip_b64,
                target_project_id=request.project_id,
                actor=request.actor,
                tenant_id=http_request.state.principal.tenant_id,
            )
        except PolicyViolation as exc:
            raise HTTPException(
                status_code=422,
                detail={"message": str(exc), "report": exc.report},
            ) from exc
        except TenantViolation as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except WorkflowError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.post("/projects/import-archive", status_code=status.HTTP_201_CREATED)
    def import_archive_package(request: ArchiveImportRequest, http_request: Request) -> dict:
        try:
            return service.import_archive_package(
                archive_path=request.archive_zip,
                archive_b64=request.archive_zip_b64,
                target_project_id=request.project_id,
                actor=request.actor,
                tenant_id=http_request.state.principal.tenant_id,
            )
        except PolicyViolation as exc:
            raise HTTPException(
                status_code=422,
                detail={"message": str(exc), "report": exc.report},
            ) from exc
        except TenantViolation as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except WorkflowError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.get("/projects/{project_id}")
    def get_project(project_id: str) -> dict:
        try:
            return service.project_view(project_id)
        except ProjectNotFound as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.patch("/projects/{project_id}")
    def update_project(project_id: str, update: ProjectUpdateRequest) -> dict:
        try:
            return service.update_project_brief(
                project_id,
                changes=update.model_dump(exclude_unset=True),
                actor=update.actor,
            )
        except ProjectNotFound as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except PolicyViolation as exc:
            raise HTTPException(
                status_code=422,
                detail={"message": str(exc), "report": exc.report},
            ) from exc
        except WorkflowError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.get("/projects/{project_id}/narrative-events")
    def list_narrative_events(project_id: str) -> dict[str, Any]:
        try:
            return service.narrative_events_view(project_id)
        except ProjectNotFound as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post(
        "/projects/{project_id}/narrative-events",
        status_code=status.HTTP_201_CREATED,
    )
    def create_narrative_event(
        project_id: str,
        payload: NarrativeEventCreateRequest,
    ) -> dict[str, Any]:
        try:
            return service.create_narrative_event(
                project_id,
                payload,
                actor=payload.actor,
            )
        except ProjectNotFound as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except WorkflowError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.patch("/projects/{project_id}/narrative-events/{event_id}")
    def update_narrative_event(
        project_id: str,
        event_id: str,
        payload: NarrativeEventUpdateRequest,
    ) -> dict[str, Any]:
        try:
            return service.update_narrative_event(
                project_id,
                event_id,
                changes=payload.model_dump(
                    exclude_unset=True,
                    exclude={"actor", "expected_revision"},
                ),
                expected_revision=payload.expected_revision,
                actor=payload.actor,
            )
        except ProjectNotFound as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except WorkflowError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.post("/projects/{project_id}/narrative-events/{event_id}/review")
    def review_narrative_event(
        project_id: str,
        event_id: str,
        payload: NarrativeEventReviewRequest,
    ) -> dict[str, Any]:
        try:
            return service.review_narrative_event(
                project_id,
                event_id,
                status=ReviewStatus(payload.status),
                comment=payload.comment,
                expected_revision=payload.expected_revision,
                actor=payload.actor,
            )
        except ProjectNotFound as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except WorkflowError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.get("/projects/{project_id}/adaptation-scenes")
    def list_adaptation_scenes(project_id: str) -> dict[str, Any]:
        try:
            return service.adaptation_scenes_view(project_id)
        except ProjectNotFound as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post(
        "/projects/{project_id}/adaptation-scenes",
        status_code=status.HTTP_201_CREATED,
    )
    def create_adaptation_scene(
        project_id: str,
        payload: AdaptationSceneCreateRequest,
    ) -> dict[str, Any]:
        try:
            return service.create_adaptation_scene(
                project_id,
                payload,
                actor=payload.actor,
            )
        except ProjectNotFound as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except WorkflowError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.post(
        "/projects/{project_id}/adaptation-scenes/derive",
        status_code=status.HTTP_201_CREATED,
    )
    def derive_adaptation_scenes(
        project_id: str,
        payload: AdaptationSceneDeriveRequest,
    ) -> dict[str, Any]:
        try:
            return service.derive_adaptation_scenes(project_id, actor=payload.actor)
        except ProjectNotFound as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except WorkflowError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.patch("/projects/{project_id}/adaptation-scenes/{scene_id}")
    def update_adaptation_scene(
        project_id: str,
        scene_id: str,
        payload: AdaptationSceneUpdateRequest,
    ) -> dict[str, Any]:
        try:
            return service.update_adaptation_scene(
                project_id,
                scene_id,
                changes=payload.model_dump(
                    exclude_unset=True,
                    exclude={"actor", "expected_revision"},
                ),
                expected_revision=payload.expected_revision,
                actor=payload.actor,
            )
        except ProjectNotFound as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except WorkflowError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.post("/projects/{project_id}/adaptation-scenes/{scene_id}/review")
    def review_adaptation_scene(
        project_id: str,
        scene_id: str,
        payload: AdaptationSceneReviewRequest,
    ) -> dict[str, Any]:
        try:
            return service.review_adaptation_scene(
                project_id,
                scene_id,
                status=ReviewStatus(payload.status),
                comment=payload.comment,
                expected_revision=payload.expected_revision,
                actor=payload.actor,
            )
        except ProjectNotFound as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except WorkflowError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.get("/projects/{project_id}/source-chapters")
    def list_source_chapters(project_id: str) -> dict[str, Any]:
        try:
            return service.source_chapters_view(project_id)
        except ProjectNotFound as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post(
        "/projects/{project_id}/source-chapters",
        status_code=status.HTTP_201_CREATED,
    )
    def create_source_chapter(
        project_id: str,
        payload: SourceChapterCreateRequest,
    ) -> dict[str, Any]:
        try:
            return service.create_source_chapter(
                project_id,
                payload,
                actor=payload.actor,
            )
        except ProjectNotFound as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except WorkflowError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.post(
        "/projects/{project_id}/source-documents/import",
        status_code=status.HTTP_201_CREATED,
    )
    def import_source_document(
        project_id: str,
        payload: SourceDocumentImportRequest,
    ) -> dict[str, Any]:
        try:
            return service.import_source_document(
                project_id,
                name=payload.name,
                content_b64=payload.content_b64,
                source_name=payload.source_name,
                rights_basis=payload.rights_basis,
                allow_external_processing=payload.allow_external_processing,
                chapter_number_start=payload.chapter_number_start,
                actor=payload.actor,
            )
        except ProjectNotFound as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except WorkflowError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.get("/projects/{project_id}/source-documents/{document_id}/download")
    def download_source_document(
        project_id: str,
        document_id: str,
    ) -> FileResponse:
        try:
            document, path = service.source_document_download(project_id, document_id)
        except ProjectNotFound as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except WorkflowError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        media_type = {
            "txt": "text/plain; charset=utf-8",
            "md": "text/markdown; charset=utf-8",
            "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            "pdf": "application/pdf",
        }[document.format]
        return FileResponse(path, media_type=media_type, filename=document.name)

    @app.patch("/projects/{project_id}/source-chapters/{chapter_id}")
    def update_source_chapter(
        project_id: str,
        chapter_id: str,
        payload: SourceChapterUpdateRequest,
    ) -> dict[str, Any]:
        try:
            return service.update_source_chapter(
                project_id,
                chapter_id,
                changes=payload.model_dump(
                    exclude_unset=True,
                    exclude={"actor", "expected_revision"},
                ),
                expected_revision=payload.expected_revision,
                actor=payload.actor,
            )
        except ProjectNotFound as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except WorkflowError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.post(
        "/projects/{project_id}/source-chapters/{chapter_id}/event-candidates",
        status_code=status.HTTP_201_CREATED,
    )
    def extract_narrative_event_candidates(
        project_id: str,
        chapter_id: str,
        payload: NarrativeCandidateExtractRequest,
    ) -> dict[str, Any]:
        try:
            return service.extract_narrative_event_candidates(
                project_id,
                chapter_id,
                actor=payload.actor,
            )
        except ProjectNotFound as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except WorkflowError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.post("/projects/{project_id}/narrative-candidates/{candidate_id}/adopt")
    def adopt_narrative_event_candidate(
        project_id: str,
        candidate_id: str,
        payload: NarrativeCandidateAdoptRequest,
    ) -> dict[str, Any]:
        try:
            return service.adopt_narrative_event_candidate(
                project_id,
                candidate_id,
                characters=payload.characters,
                event_id=payload.event_id,
                expected_revision=payload.expected_revision,
                actor=payload.actor,
            )
        except ProjectNotFound as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except WorkflowError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.post("/projects/{project_id}/narrative-candidates/{candidate_id}/discard")
    def discard_narrative_event_candidate(
        project_id: str,
        candidate_id: str,
        payload: NarrativeCandidateDiscardRequest,
    ) -> dict[str, Any]:
        try:
            return service.discard_narrative_event_candidate(
                project_id,
                candidate_id,
                reason=payload.reason,
                expected_revision=payload.expected_revision,
                actor=payload.actor,
            )
        except ProjectNotFound as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except WorkflowError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.get("/projects/{project_id}/events")
    async def project_events(
        project_id: str,
        after: int = Query(default=0, ge=0),
        timeout_seconds: float = Query(default=25, ge=1, le=60),
    ) -> StreamingResponse:
        try:
            service.audit_log(project_id)
        except ProjectNotFound as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

        async def stream():
            cursor = after
            deadline = asyncio.get_running_loop().time() + timeout_seconds
            while asyncio.get_running_loop().time() < deadline:
                payload = service.audit_log(project_id)
                events = payload.get("events", [])
                if cursor < len(events):
                    for index in range(cursor, len(events)):
                        yield (
                            f"id: {index + 1}\n"
                            "event: audit\n"
                            f"data: {json.dumps(events[index], ensure_ascii=False)}\n\n"
                        )
                    cursor = len(events)
                else:
                    yield ": heartbeat\n\n"
                await asyncio.sleep(0.5)
            yield "event: close\ndata: {}\n\n"

        return StreamingResponse(
            stream(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    @app.get("/projects/{project_id}/collaboration")
    def get_project_collaboration(project_id: str) -> dict[str, Any]:
        try:
            return service.project_collaboration(project_id)
        except ProjectNotFound as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.get("/projects/{project_id}/collaboration/documents")
    def get_collaboration_documents(project_id: str) -> dict[str, Any]:
        try:
            return service.collaboration_documents(project_id)
        except ProjectNotFound as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.get("/projects/{project_id}/collaboration/events")
    def get_collaboration_events(
        project_id: str,
        after: int = Query(default=0, ge=0),
    ) -> dict[str, Any]:
        try:
            return service.collaboration_events(project_id, after=after)
        except ProjectNotFound as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post("/projects/{project_id}/collaboration/documents/{document_id}/operations")
    def apply_collaboration_document_operations(
        project_id: str,
        document_id: str,
        payload: CollaborationDocumentOperationsRequest,
        request: Request,
    ) -> dict[str, Any]:
        try:
            return service.apply_collaboration_text_operations(
                project_id,
                document_id=document_id,
                operations=payload.operations,
                actor=request.state.principal.subject,
            )
        except ProjectNotFound as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except WorkflowError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.put("/projects/{project_id}/collaboration/documents/{document_id}")
    def replace_collaboration_document(
        project_id: str,
        document_id: str,
        payload: CollaborationDocumentReplaceRequest,
        request: Request,
    ) -> dict[str, Any]:
        try:
            return service.replace_collaboration_document(
                project_id,
                document_id=document_id,
                text=payload.text,
                actor=request.state.principal.subject,
            )
        except ProjectNotFound as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except WorkflowError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.websocket("/projects/{project_id}/collaboration/events/ws")
    async def collaboration_events_websocket(websocket: WebSocket, project_id: str) -> None:
        """Replay durable collaboration events without placing bearer tokens in URLs."""
        origin = websocket.headers.get("origin")
        host = websocket.headers.get("host", "")
        configured_origins = {
            value.strip()
            for value in os.getenv("MEDIAFORGE_ALLOWED_WEBSOCKET_ORIGINS", "").split(",")
            if value.strip()
        }
        allowed_origins = configured_origins or {f"http://{host}", f"https://{host}"}
        if origin and origin not in allowed_origins:
            await websocket.close(code=4403, reason="websocket origin is not allowed")
            return

        await websocket.accept()
        principal: Principal | None = None
        try:
            principal = auth_manager.authenticate(
                websocket.headers.get("authorization"),
                session_id=websocket.cookies.get("mediaforge_session"),
            )
        except AuthenticationError:
            try:
                initial = await asyncio.wait_for(websocket.receive_json(), timeout=5)
                token = str(initial.get("token") or "").strip() if isinstance(initial, dict) else ""
                principal = auth_manager.authenticate(f"Bearer {token}")
            except (asyncio.TimeoutError, AuthenticationError, ValueError, WebSocketDisconnect):
                await websocket.close(code=4401, reason="authentication is required")
                return

        try:
            project = service.projects.get(project_id)
            if project is None:
                await websocket.close(code=4404, reason="project not found")
                return
            if principal.tenant_id and project.brief.tenant_id != principal.tenant_id:
                await websocket.close(code=4404, reason="project not found")
                return
            if principal.role != "admin":
                member_role = service.project_member_role(project_id, principal.subject)
                if service.project_role_levels().get(member_role or "", 0) < service.project_role_levels()["viewer"]:
                    await websocket.close(code=4403, reason="project membership is required")
                    return
            if service.enterprise.control_plane.enabled and not service.control_plane_status(acquire=True).get("ready_for_traffic"):
                await websocket.close(code=1013, reason="control plane is not ready")
                return
            try:
                cursor = max(0, int(websocket.query_params.get("after", "0")))
            except ValueError:
                cursor = 0
            await websocket.send_json(
                {
                    "type": "snapshot",
                    "data": service.collaboration_realtime_snapshot(project_id),
                }
            )
            last_heartbeat = perf_counter()
            while True:
                replay = service.collaboration_events(project_id, after=cursor)
                for event in replay["events"]:
                    await websocket.send_json({"type": "event", "data": event})
                    cursor = max(cursor, int(event["event_id"]))
                if perf_counter() - last_heartbeat >= 20:
                    await websocket.send_json({"type": "heartbeat", "cursor": cursor})
                    last_heartbeat = perf_counter()
                try:
                    message = await asyncio.wait_for(websocket.receive(), timeout=0.5)
                    if message.get("type") == "websocket.disconnect":
                        return
                except asyncio.TimeoutError:
                    continue
        except WebSocketDisconnect:
            return

    @app.post("/projects/{project_id}/collaboration/presence")
    def update_collaboration_presence(
        project_id: str,
        presence: CollaborationPresenceRequest,
        request: Request,
    ) -> dict[str, Any]:
        try:
            return service.update_collaboration_presence(
                project_id,
                subject=request.state.principal.subject,
                status=presence.status,
                section=presence.section,
                ttl_seconds=presence.ttl_seconds,
            )
        except ProjectNotFound as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except WorkflowError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.post("/projects/{project_id}/collaboration/locks")
    def acquire_project_edit_lock(
        project_id: str,
        edit_lock: EditLockRequest,
        request: Request,
    ) -> dict[str, Any]:
        try:
            return service.acquire_edit_lock(
                project_id,
                subject=request.state.principal.subject,
                target_type=edit_lock.target_type,
                target_id=edit_lock.target_id,
                ttl_seconds=edit_lock.ttl_seconds,
            )
        except ProjectNotFound as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except WorkflowError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.delete("/projects/{project_id}/collaboration/locks/{lock_id}")
    def release_project_edit_lock(
        project_id: str,
        lock_id: str,
        request: Request,
    ) -> dict[str, Any]:
        try:
            return service.release_edit_lock(
                project_id,
                lock_id,
                subject=request.state.principal.subject,
            )
        except ProjectNotFound as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except WorkflowError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.post("/projects/{project_id}/collaboration/members")
    def upsert_project_member(
        project_id: str,
        member: ProjectMemberRequest,
        request: Request,
    ) -> dict[str, Any]:
        try:
            return service.upsert_project_member(
                project_id,
                subject=member.subject,
                role=member.role,
                actor=request.state.principal.subject,
            )
        except ProjectNotFound as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except WorkflowError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.delete("/projects/{project_id}/collaboration/members/{subject}")
    def remove_project_member(
        project_id: str,
        subject: str,
        request: Request,
    ) -> dict[str, Any]:
        try:
            return service.remove_project_member(
                project_id,
                subject,
                actor=request.state.principal.subject,
            )
        except ProjectNotFound as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except WorkflowError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.get("/projects/{project_id}/comments")
    def list_project_comments(project_id: str) -> dict[str, Any]:
        try:
            collaboration = service.project_collaboration(project_id)
            return {
                "project_id": project_id,
                "comments": collaboration["comments"],
                "comment_count": collaboration["comment_count"],
            }
        except ProjectNotFound as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post("/projects/{project_id}/comments", status_code=status.HTTP_201_CREATED)
    def add_project_comment(
        project_id: str,
        comment: ProjectCommentRequest,
        request: Request,
    ) -> dict[str, Any]:
        try:
            return service.add_project_comment(
                project_id,
                body=comment.body,
                author=request.state.principal.subject,
                shot_id=comment.shot_id,
            )
        except ProjectNotFound as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ShotNotFound as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except WorkflowError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.delete("/projects/{project_id}/comments/{comment_id}")
    def remove_project_comment(
        project_id: str,
        comment_id: str,
        request: Request,
    ) -> dict[str, Any]:
        try:
            return service.remove_project_comment(
                project_id,
                comment_id,
                actor=request.state.principal.subject,
            )
        except ProjectNotFound as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except WorkflowError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.post("/projects/{project_id}/clone", status_code=status.HTTP_201_CREATED)
    def clone_project(project_id: str, request: CloneRequest, http_request: Request) -> dict:
        try:
            return service.clone_project(
                project_id,
                target_project_id=request.project_id,
                title_suffix=request.title_suffix,
                actor=request.actor,
                tenant_id=http_request.state.principal.tenant_id,
            )
        except ProjectNotFound as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except PolicyViolation as exc:
            raise HTTPException(
                status_code=422,
                detail={"message": str(exc), "report": exc.report},
            ) from exc
        except TenantViolation as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except WorkflowError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.post("/projects/{project_id}/snapshot/export")
    def export_project_snapshot(project_id: str, request: ActorRequest) -> dict:
        try:
            return service.export_project_snapshot(project_id, actor=request.actor)
        except ProjectNotFound as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except WorkflowError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.post("/projects/{project_id}/archive")
    def archive_project(project_id: str) -> dict:
        try:
            return service.archive_project(project_id)
        except ProjectNotFound as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post("/projects/{project_id}/restore")
    def restore_project(project_id: str) -> dict:
        try:
            return service.restore_project(project_id)
        except ProjectNotFound as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.get("/projects/{project_id}/cost")
    def get_project_cost(project_id: str) -> dict:
        try:
            return service.cost_report(project_id)
        except ProjectNotFound as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.get("/projects/{project_id}/policy")
    def get_project_policy(project_id: str) -> dict:
        try:
            return service.policy_report(project_id)
        except ProjectNotFound as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.get("/projects/{project_id}/assets")
    def get_project_assets(project_id: str) -> dict:
        try:
            return service.asset_inventory(project_id)
        except ProjectNotFound as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post("/projects/{project_id}/assets/{asset_id}/derivatives")
    def generate_project_asset_derivatives(
        project_id: str,
        asset_id: str,
        request: AssetDerivativeRequest,
    ) -> dict:
        try:
            return service.asset_derivatives(project_id, asset_id, actor=request.actor)
        except ProjectNotFound as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except WorkflowError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.get("/projects/{project_id}/assets/{asset_id}/derivatives/{derivative_id}/download")
    def download_project_asset_derivative(
        project_id: str,
        asset_id: str,
        derivative_id: str,
    ) -> FileResponse:
        try:
            derivative = service.asset_derivative_download(
                project_id, asset_id, derivative_id
            )
            return FileResponse(
                derivative["path"],
                media_type=derivative["media_type"],
                filename=derivative["filename"],
                headers={
                    "X-MediaForge-Asset-Id": asset_id,
                    "X-MediaForge-Derivative-Id": derivative_id,
                    "X-MediaForge-SHA256": derivative["sha256"],
                },
            )
        except (ProjectNotFound, WorkflowError) as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.get("/projects/{project_id}/assets/{asset_id}/download")
    def download_project_asset(project_id: str, asset_id: str) -> FileResponse:
        try:
            asset = service.asset_download(project_id, asset_id)
            return FileResponse(
                asset["path"],
                media_type=asset["media_type"],
                filename=asset["filename"],
                headers={
                    "X-MediaForge-Asset-Id": asset["asset_id"],
                    "X-MediaForge-SHA256": asset["sha256"],
                },
            )
        except ProjectNotFound as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except WorkflowError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.get("/projects/{project_id}/references")
    def get_project_references(project_id: str) -> dict:
        try:
            inventory = service.asset_inventory(project_id)
            return {
                "project_id": project_id,
                "reference_assets": inventory["reference_assets"],
            }
        except ProjectNotFound as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post("/projects/{project_id}/references", status_code=status.HTTP_201_CREATED)
    def register_project_reference(
        project_id: str,
        request: ReferenceAssetRequest,
    ) -> dict:
        try:
            return service.register_reference_asset(
                project_id,
                name=request.name,
                content_b64=request.content_b64,
                license=request.license,
                kind=request.kind,
                character=request.character,
                source=request.source,
                actor=request.actor,
            )
        except ProjectNotFound as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except WorkflowError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.post("/projects/{project_id}/audio", status_code=status.HTTP_201_CREATED)
    def register_project_audio(
        project_id: str,
        request: AudioTrackRequest,
    ) -> dict:
        try:
            return service.register_audio_track(
                project_id,
                name=request.name,
                content_b64=request.content_b64,
                license=request.license,
                source=request.source,
                actor=request.actor,
            )
        except ProjectNotFound as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except WorkflowError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.post("/projects/{project_id}/voiceover", status_code=status.HTTP_201_CREATED)
    def generate_project_voiceover(project_id: str, request: VoiceoverRequest) -> dict:
        try:
            return service.generate_voiceover(project_id, text=request.text, voice=request.voice, language=request.language, license=request.license, source=request.source, actor=request.actor)
        except ProjectNotFound as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except (WorkflowError, SpeechSynthesisError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.post("/projects/{project_id}/dialogue", status_code=status.HTTP_201_CREATED)
    def generate_project_dialogue(
        project_id: str, request: DialogueTimelineRequest
    ) -> dict:
        try:
            return service.generate_dialogue_timeline(
                project_id,
                lines=request.lines,
                license=request.license,
                source=request.source,
                actor=request.actor,
            )
        except ProjectNotFound as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except (WorkflowError, SpeechSynthesisError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.post("/projects/{project_id}/lipsync", status_code=status.HTTP_201_CREATED)
    def generate_project_lipsync(project_id: str, request: ActorRequest) -> dict:
        try:
            return service.generate_lipsync(project_id, actor=request.actor)
        except ProjectNotFound as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except WorkflowError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.delete("/projects/{project_id}/audio")
    def remove_project_audio(project_id: str, request: ActorRequest) -> dict:
        try:
            return service.remove_audio_track(project_id, actor=request.actor)
        except ProjectNotFound as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except WorkflowError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.get("/projects/{project_id}/timeline")
    def get_project_timeline(project_id: str) -> dict:
        try:
            return service.edit_timeline(project_id)
        except ProjectNotFound as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.put("/projects/{project_id}/timeline")
    def update_project_timeline(
        project_id: str,
        request: TimelineUpdateRequest,
    ) -> dict:
        try:
            return service.update_edit_timeline(
                project_id,
                clips=request.clips,
                actor=request.actor,
            )
        except ProjectNotFound as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except WorkflowError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.post("/projects/{project_id}/timeline/export")
    def export_project_timeline(
        project_id: str,
        request: TimelineExportRequest,
    ) -> dict:
        try:
            return service.export_edit_timeline(
                project_id,
                format=request.format,
                actor=request.actor,
            )
        except ProjectNotFound as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except WorkflowError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.get("/projects/{project_id}/operations")
    def get_project_operations(project_id: str) -> dict:
        try:
            return service.operations_summary(project_id)
        except ProjectNotFound as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.get("/projects/{project_id}/workflow")
    def get_project_workflow(project_id: str) -> dict:
        try:
            return service.production_workflow(project_id)
        except ProjectNotFound as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post("/projects/{project_id}/workflow/stages/{stage_key}/lock")
    def lock_project_workflow_stage(
        project_id: str,
        stage_key: str,
        request: ActorRequest,
    ) -> dict:
        try:
            return service.lock_production_stage(
                project_id,
                stage_key,
                actor=request.actor,
            )
        except ProjectNotFound as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except WorkflowError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.post("/projects/{project_id}/workflow/stages/{stage_key}/return")
    def return_project_workflow_stage(
        project_id: str,
        stage_key: str,
        request: ProductionStageReturnRequest,
    ) -> dict:
        try:
            return service.return_production_stage(
                project_id,
                stage_key,
                reason=request.reason,
                actor=request.actor,
            )
        except ProjectNotFound as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except WorkflowError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.get("/projects/{project_id}/reports/production")
    def get_project_production_report(project_id: str) -> dict:
        try:
            return service.production_report(project_id)
        except ProjectNotFound as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post("/projects/{project_id}/reports/export")
    def export_project_production_report(
        project_id: str,
        request: ActorRequest,
    ) -> dict:
        try:
            return service.export_production_report(
                project_id,
                actor=request.actor,
            )
        except ProjectNotFound as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except WorkflowError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.get("/projects/{project_id}/trace")
    def get_project_trace(project_id: str) -> dict:
        try:
            return service.project_trace(project_id)
        except ProjectNotFound as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post("/projects/{project_id}/trace/export")
    def export_project_trace(project_id: str, request: ActorRequest) -> dict:
        try:
            return service.export_project_trace(project_id, actor=request.actor)
        except ProjectNotFound as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except WorkflowError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.get("/projects/{project_id}/retrospective")
    def get_project_retrospective(project_id: str) -> dict:
        try:
            return service.retrospective_report(project_id)
        except ProjectNotFound as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post("/projects/{project_id}/retrospective/export")
    def export_project_retrospective(
        project_id: str,
        request: ActorRequest,
    ) -> dict:
        try:
            return service.export_retrospective_report(
                project_id,
                actor=request.actor,
            )
        except ProjectNotFound as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except WorkflowError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.get("/projects/{project_id}/provenance")
    def get_project_provenance(project_id: str) -> dict:
        try:
            return service.project_provenance(project_id)
        except ProjectNotFound as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.get("/projects/{project_id}/content-credentials")
    def get_project_content_credentials(project_id: str) -> dict[str, Any]:
        try:
            return service.project_content_credentials(project_id)
        except ProjectNotFound as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.get("/content-credentials/status")
    def get_content_credentials_status() -> dict[str, Any]:
        return service.content_credentials_status()

    @app.post("/projects/{project_id}/content-credentials")
    def create_project_content_credential(
        project_id: str,
        credential: ContentCredentialRequest,
    ) -> dict[str, Any]:
        try:
            return service.create_content_credential(
                project_id,
                asset_id=credential.asset_id,
                actor=credential.actor,
            )
        except ProjectNotFound as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except WorkflowError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.post(
        "/projects/{project_id}/content-credentials/{credential_id}/verify"
    )
    def verify_project_content_credential(
        project_id: str,
        credential_id: str,
        request: ActorRequest,
    ) -> dict[str, Any]:
        try:
            return service.verify_content_credential(
                project_id,
                credential_id,
                actor=request.actor,
            )
        except ProjectNotFound as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except WorkflowError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.post("/projects/{project_id}/provenance/export")
    def export_project_provenance(
        project_id: str,
        request: ActorRequest,
    ) -> dict:
        try:
            return service.export_project_provenance(
                project_id,
                actor=request.actor,
            )
        except ProjectNotFound as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except WorkflowError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.get("/projects/{project_id}/compliance")
    def get_project_compliance(project_id: str) -> dict:
        try:
            return service.project_compliance(project_id)
        except ProjectNotFound as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post("/projects/{project_id}/compliance/export")
    def export_project_compliance(
        project_id: str,
        request: ActorRequest,
    ) -> dict:
        try:
            return service.export_project_compliance(
                project_id,
                actor=request.actor,
            )
        except ProjectNotFound as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except WorkflowError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.get("/projects/{project_id}/continuity")
    def get_project_continuity(project_id: str) -> dict:
        try:
            return service.project_continuity(project_id)
        except ProjectNotFound as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post("/projects/{project_id}/continuity/export")
    def export_project_continuity(
        project_id: str,
        request: ActorRequest,
    ) -> dict:
        try:
            return service.export_project_continuity(
                project_id,
                actor=request.actor,
            )
        except ProjectNotFound as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except WorkflowError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.get("/projects/{project_id}/distribution")
    def get_project_distribution(project_id: str) -> dict:
        try:
            return service.distribution_report(project_id)
        except ProjectNotFound as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post("/projects/{project_id}/distribution/export")
    def export_project_distribution(
        project_id: str,
        request: ActorRequest,
    ) -> dict:
        try:
            return service.export_distribution_report(
                project_id,
                actor=request.actor,
            )
        except ProjectNotFound as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except WorkflowError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.get("/projects/{project_id}/routes")
    def get_project_routes(project_id: str) -> dict:
        try:
            return service.project_routes(project_id)
        except ProjectNotFound as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.get("/projects/{project_id}/shots")
    def get_project_shots(
        project_id: str,
        review_status: str | None = Query(default=None),
        query: str | None = Query(default=None, max_length=200),
        limit: int = Query(default=200, ge=1, le=500),
    ) -> dict:
        try:
            parsed_status = (
                ReviewStatus(review_status)
                if review_status
                else None
            )
            return service.project_shots(
                project_id,
                review_status=parsed_status,
                query=query,
                limit=limit,
            )
        except ProjectNotFound as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.patch("/projects/{project_id}/shots/{shot_id}")
    def update_shot(
        project_id: str,
        shot_id: str,
        update: ShotUpdateRequest,
    ) -> dict:
        try:
            return service.update_shot_card(
                project_id,
                shot_id,
                changes=update.model_dump(exclude_unset=True),
                actor=update.actor,
            )
        except (ProjectNotFound, ShotNotFound) as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except WorkflowError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.get("/projects/{project_id}/jobs")
    def get_project_jobs(
        project_id: str,
        job_status: str | None = Query(default=None, alias="status"),
        shot_id: str | None = Query(default=None),
        limit: int = Query(default=200, ge=1, le=500),
    ) -> dict:
        try:
            parsed_status = JobStatus(job_status) if job_status else None
            return service.project_jobs(
                project_id,
                status=parsed_status,
                shot_id=shot_id,
                limit=limit,
            )
        except ProjectNotFound as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.get("/projects/{project_id}/audit")
    def get_project_audit(project_id: str) -> dict:
        try:
            return service.audit_log(project_id)
        except ProjectNotFound as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.get("/projects/{project_id}/audit/integrity")
    def get_project_audit_integrity(project_id: str) -> dict:
        try:
            return service.audit_integrity_report(project_id)
        except ProjectNotFound as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.get("/projects/{project_id}/audit/anchors")
    def get_project_audit_anchors(project_id: str) -> dict:
        try:
            return service.audit_anchor_report(project_id)
        except ProjectNotFound as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post("/projects/{project_id}/audit/anchor")
    def anchor_project_audit(project_id: str, request: ActorRequest) -> dict:
        try:
            return service.anchor_audit_chain(project_id, actor=request.actor)
        except ProjectNotFound as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except WorkflowError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.post("/projects/{project_id}/audit/export")
    def export_project_audit(project_id: str) -> dict:
        try:
            return service.export_audit_log(project_id)
        except ProjectNotFound as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except WorkflowError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.post("/projects/{project_id}/audit/export-csv")
    def export_project_audit_csv(project_id: str, request: ActorRequest) -> dict:
        try:
            return service.export_audit_csv(project_id, actor=request.actor)
        except ProjectNotFound as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except WorkflowError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.post("/projects/{project_id}/audit/export-integrity")
    def export_project_audit_integrity(project_id: str) -> dict:
        try:
            return service.export_audit_integrity_report(project_id)
        except ProjectNotFound as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except WorkflowError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.get("/projects/{project_id}/evaluations/latest")
    def get_project_evaluation(project_id: str) -> dict:
        try:
            return service.latest_evaluation_report(project_id)
        except ProjectNotFound as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post("/projects/{project_id}/evaluations/run")
    def run_project_evaluation(project_id: str, request: ActorRequest) -> dict:
        try:
            return service.run_project_evaluation(project_id, actor=request.actor)
        except ProjectNotFound as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except WorkflowError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.get("/projects/{project_id}/evaluations/baselines")
    def get_project_evaluation_baselines(project_id: str) -> dict:
        try:
            evaluation = service.latest_evaluation_report(project_id)
            return {
                "project_id": project_id,
                "baselines": evaluation["baselines"],
                "regression": evaluation["regression"],
            }
        except ProjectNotFound as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post("/projects/{project_id}/evaluations/baselines", status_code=status.HTTP_201_CREATED)
    def create_project_evaluation_baseline(
        project_id: str,
        request: EvaluationBaselineRequest,
    ) -> dict:
        try:
            return service.create_evaluation_baseline(
                project_id,
                name=request.name,
                minimum_score=request.minimum_score,
                actor=request.actor,
            )
        except ProjectNotFound as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except WorkflowError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.get("/projects/{project_id}/llmops")
    def get_project_llmops(project_id: str) -> dict:
        try:
            return service.llmops_summary(project_id)
        except ProjectNotFound as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.get("/projects/{project_id}/prompts")
    def get_project_prompts(project_id: str) -> dict:
        try:
            return service.prompt_registry(project_id)
        except ProjectNotFound as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post("/projects/{project_id}/prompts", status_code=status.HTTP_201_CREATED)
    def create_project_prompt(
        project_id: str,
        request: PromptVersionRequest,
    ) -> dict:
        try:
            return service.create_prompt_version(
                project_id,
                key=request.key,
                template=request.template,
                label=request.label,
                activate=request.activate,
                actor=request.actor,
            )
        except ProjectNotFound as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except WorkflowError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.post("/projects/{project_id}/prompts/{prompt_id}/activate")
    def activate_project_prompt(
        project_id: str,
        prompt_id: str,
        request: PromptActivateRequest,
    ) -> dict:
        try:
            return service.activate_prompt_version(
                project_id, prompt_id, actor=request.actor
            )
        except ProjectNotFound as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except WorkflowError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.post("/projects/{project_id}/evaluations/annotations", status_code=status.HTTP_201_CREATED)
    def create_project_evaluation_annotation(
        project_id: str,
        request: EvaluationAnnotationRequest,
    ) -> dict:
        try:
            return service.record_evaluation_annotation(
                project_id,
                target_type=request.target_type,
                target_id=request.target_id,
                verdict=request.verdict,
                note=request.note,
                rating=request.rating,
                actor=request.actor,
            )
        except ProjectNotFound as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except (WorkflowError, ShotNotFound) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.post("/projects/{project_id}/experiments", status_code=status.HTTP_201_CREATED)
    def create_project_prompt_experiment(
        project_id: str,
        request: PromptExperimentRequest,
    ) -> dict:
        try:
            return service.register_prompt_experiment(
                project_id,
                name=request.name,
                prompt_key=request.prompt_key,
                control_prompt_id=request.control_prompt_id,
                treatment_prompt_id=request.treatment_prompt_id,
                objective=request.objective,
                actor=request.actor,
            )
        except ProjectNotFound as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except WorkflowError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.get("/projects/{project_id}/training-dataset")
    def get_training_dataset(project_id: str) -> dict:
        try:
            return service.training_dataset(project_id)
        except ProjectNotFound as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post("/projects/{project_id}/training-dataset/export")
    def export_training_dataset(project_id: str, request: ActorRequest) -> dict:
        try:
            return service.export_training_dataset(project_id, actor=request.actor)
        except ProjectNotFound as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except WorkflowError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.get("/projects/{project_id}/providers/benchmark")
    def get_provider_benchmark(project_id: str) -> dict:
        try:
            return service.latest_provider_benchmark(project_id)
        except ProjectNotFound as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post("/projects/{project_id}/providers/benchmark")
    def run_provider_benchmark(project_id: str, request: ActorRequest) -> dict:
        try:
            return service.run_provider_benchmark(project_id, actor=request.actor)
        except ProjectNotFound as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except WorkflowError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.post("/projects/{project_id}/plan")
    def generate_plan(project_id: str, request: Request, payload: PlanningRequest | None = None) -> dict:
        try:
            principal = request.state.principal
            return service.generate_plan(project_id, memory_project_ids=payload.memory_project_ids if payload else None,
                                         subject=None if principal.role == "admin" else principal.subject)
        except ProjectNotFound as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except PolicyViolation as exc:
            raise HTTPException(
                status_code=422,
                detail={"message": str(exc), "report": exc.report},
            ) from exc
        except StoryPlannerError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        except MemoryUnavailable as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        except WorkflowError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.exception_handler(PlanningError)
    async def planning_error_handler(request: Request, exc: PlanningError):
        code = 404 if isinstance(exc, PlanningNotFound) else 409
        return JSONResponse(status_code=code, content={'detail': str(exc)})

    @app.get('/planning/status')
    def planning_status():
        return service.planning.status_view()

    @app.get('/projects/{project_id}/planning')
    def planning_runs(project_id: str):
        return service.planning.list_runs(project_id)

    @app.post('/projects/{project_id}/planning')
    def start_planning(project_id: str, request: Request, payload: PlanningRequest | None = None):
        principal = request.state.principal
        return service.planning.start(project_id, payload.memory_project_ids if payload else None,
                                      subject=None if principal.role == 'admin' else principal.subject)

    @app.get('/projects/{project_id}/planning/{run_id}')
    def planning_run(project_id: str, run_id: str):
        return service.planning.view(project_id, run_id)

    @app.post('/projects/{project_id}/planning/{run_id}/resume')
    def resume_planning(project_id: str, run_id: str, request: Request):
        principal = request.state.principal
        return service.planning.resume(project_id, run_id, subject=None if principal.role == 'admin' else principal.subject)

    @app.post('/projects/{project_id}/planning/{run_id}/migrate')
    def migrate_planning(project_id: str, run_id: str, request: Request):
        principal = request.state.principal
        return service.planning.migrate(
            project_id,
            run_id,
            actor=principal.subject,
            subject=None if principal.role == 'admin' else principal.subject,
        )

    @app.post('/projects/{project_id}/planning/{run_id}/review')
    def review_planning(project_id: str, run_id: str, payload: PlanningReviewRequest, request: Request):
        principal = request.state.principal
        return service.planning.review(project_id, run_id, payload.decision, payload.comment, principal.subject,
                                       subject=None if principal.role == 'admin' else principal.subject)

    @app.post('/projects/{project_id}/planning/{run_id}/cancel')
    def cancel_planning(project_id: str, run_id: str):
        return service.planning.cancel(project_id, run_id)

    @app.post("/projects/{project_id}/shots/{shot_id}/submit")
    def submit_shot(project_id: str, shot_id: str) -> dict:
        try:
            return service.submit_shot(project_id, shot_id)
        except (ProjectNotFound, ShotNotFound) as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except PolicyViolation as exc:
            raise HTTPException(
                status_code=422,
                detail={"message": str(exc), "report": exc.report},
            ) from exc
        except WorkflowError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.post("/projects/{project_id}/shots/{shot_id}/enqueue")
    def enqueue_shot(project_id: str, shot_id: str) -> dict:
        try:
            return service.enqueue_shot(project_id, shot_id)
        except (ProjectNotFound, ShotNotFound) as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except PolicyViolation as exc:
            raise HTTPException(
                status_code=422,
                detail={"message": str(exc), "report": exc.report},
            ) from exc
        except WorkflowError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.post("/projects/{project_id}/shots/{shot_id}/retry")
    def retry_shot(project_id: str, shot_id: str) -> dict:
        try:
            return service.retry_shot(project_id, shot_id)
        except (ProjectNotFound, ShotNotFound) as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except PolicyViolation as exc:
            raise HTTPException(
                status_code=422,
                detail={"message": str(exc), "report": exc.report},
            ) from exc
        except WorkflowError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.post("/projects/{project_id}/shots/{shot_id}/retry/schedule")
    def schedule_shot_retry(
        project_id: str,
        shot_id: str,
        request: RetryScheduleRequest,
    ) -> dict:
        try:
            return service.schedule_retry(
                project_id,
                shot_id,
                delay_seconds=request.delay_seconds,
                actor=request.actor,
            )
        except (ProjectNotFound, ShotNotFound) as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except WorkflowError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.get("/projects/{project_id}/shots/{shot_id}/variants/comparison")
    def get_shot_comparison(project_id: str, shot_id: str) -> dict:
        try:
            return service.shot_comparison_report(project_id, shot_id)
        except (ProjectNotFound, ShotNotFound) as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post("/projects/{project_id}/shots/{shot_id}/variants/compare")
    def compare_shot_variants(
        project_id: str,
        shot_id: str,
        request: VariantCompareRequest,
    ) -> dict:
        try:
            return service.compare_shot_variants(
                project_id,
                shot_id,
                candidate_count=request.candidate_count,
                actor=request.actor,
            )
        except (ProjectNotFound, ShotNotFound) as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except PolicyViolation as exc:
            raise HTTPException(
                status_code=422,
                detail={"message": str(exc), "report": exc.report},
            ) from exc
        except WorkflowError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.post("/projects/{project_id}/shots/{shot_id}/variants/export")
    def export_shot_comparison(
        project_id: str,
        shot_id: str,
        request: ActorRequest,
    ) -> dict:
        try:
            return service.export_shot_comparison(
                project_id,
                shot_id,
                actor=request.actor,
            )
        except (ProjectNotFound, ShotNotFound) as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except WorkflowError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.post("/projects/{project_id}/shots/{shot_id}/variants/{variant_id}/promote")
    def promote_shot_variant(
        project_id: str,
        shot_id: str,
        variant_id: str,
        request: VariantPromoteRequest,
    ) -> dict:
        try:
            return service.promote_shot_variant(
                project_id,
                shot_id,
                variant_id,
                actor=request.actor,
                comment=request.comment,
            )
        except (ProjectNotFound, ShotNotFound) as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except WorkflowError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.get("/projects/{project_id}/shots/{shot_id}/route")
    def get_shot_route(project_id: str, shot_id: str) -> dict:
        try:
            return service.shot_route_report(project_id, shot_id)
        except (ProjectNotFound, ShotNotFound) as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post("/projects/{project_id}/shots/{shot_id}/route/export")
    def export_shot_route(
        project_id: str,
        shot_id: str,
        request: ActorRequest,
    ) -> dict:
        try:
            return service.export_shot_route_report(
                project_id,
                shot_id,
                actor=request.actor,
            )
        except (ProjectNotFound, ShotNotFound) as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except WorkflowError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.post("/projects/{project_id}/jobs/{job_id}/process")
    def process_job(
        project_id: str,
        job_id: str,
        worker_id: str | None = Query(default=None),
    ) -> dict:
        try:
            return service.process_job(
                project_id,
                job_id,
                worker_id=worker_id,
                actor=worker_id or "generation-worker",
            )
        except (ProjectNotFound, ShotNotFound, JobNotFound) as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except PolicyViolation as exc:
            raise HTTPException(
                status_code=422,
                detail={"message": str(exc), "report": exc.report},
            ) from exc
        except WorkflowError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.post("/projects/{project_id}/jobs/{job_id}/callback")
    async def provider_callback(
        project_id: str,
        job_id: str,
        payload: ProviderCallbackRequest,
        request: Request,
    ) -> dict:
        try:
            authentication = callback_security.verify(
                timestamp=request.headers.get("X-MediaForge-Timestamp"),
                signature=request.headers.get("X-MediaForge-Signature"),
                method=request.method,
                path=request.url.path,
                body=await request.body(),
            )
        except CallbackSecurityError as exc:
            raise HTTPException(
                status_code=exc.status_code,
                detail=str(exc),
                headers={"WWW-Authenticate": "HMAC-SHA256"},
            ) from exc
        try:
            result = service.provider_callback(
                project_id,
                job_id,
                event_id=payload.event_id,
                provider=payload.provider,
                status=JobStatus(payload.status),
                reason=payload.reason,
                artifact_uri=payload.artifact_uri,
                artifact_kind=payload.artifact_kind,
                mime_type=payload.mime_type,
                estimated_cost=payload.estimated_cost,
                actual_cost=payload.actual_cost,
                actor=payload.actor,
                worker_id=payload.worker_id,
                external_reference=payload.external_reference,
            )
            result["callback_authentication"] = authentication
            return result
        except (ProjectNotFound, JobNotFound, ShotNotFound) as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except WorkflowError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.post("/providers/replicate/webhook")
    async def replicate_webhook(
        request: Request,
        project_id: str = Query(min_length=1, max_length=120),
        job_id: str = Query(min_length=1, max_length=120),
    ) -> dict:
        raw_body = await request.body()
        try:
            prediction = json.loads(raw_body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise HTTPException(
                status_code=422,
                detail="Replicate webhook body must be a JSON object",
            ) from exc
        if not isinstance(prediction, dict):
            raise HTTPException(
                status_code=422,
                detail="Replicate webhook body must be a JSON object",
            )
        authentication = request.state.replicate_webhook_authentication
        try:
            result = service.reconcile_replicate_webhook(
                project_id,
                job_id,
                webhook_id=str(authentication["webhook_id"]),
                prediction=prediction,
            )
        except (ProjectNotFound, JobNotFound, ShotNotFound) as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except WorkflowError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        result["webhook_authentication"] = authentication
        return result

    @app.post("/projects/{project_id}/jobs/{job_id}/cancel")
    def cancel_job(project_id: str, job_id: str) -> dict:
        try:
            return service.cancel_job(project_id, job_id)
        except (ProjectNotFound, JobNotFound) as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except WorkflowError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.post("/projects/{project_id}/jobs/recover-stale")
    def recover_stale_project_jobs(
        project_id: str,
        request: StaleJobRecoveryRequest,
    ) -> dict:
        try:
            return service.recover_stale_jobs(
                project_id,
                stale_after_seconds=request.stale_after_seconds,
                actor=request.actor,
            )
        except ProjectNotFound as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except WorkflowError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.post("/projects/{project_id}/shots/submit-all")
    def submit_pending_shots(project_id: str) -> dict:
        try:
            return service.submit_pending_shots(project_id)
        except ProjectNotFound as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except PolicyViolation as exc:
            raise HTTPException(
                status_code=422,
                detail={"message": str(exc), "report": exc.report},
            ) from exc
        except WorkflowError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.post("/projects/{project_id}/shots/enqueue-all")
    def enqueue_pending_shots(project_id: str) -> dict:
        try:
            return service.enqueue_pending_shots(project_id)
        except ProjectNotFound as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except PolicyViolation as exc:
            raise HTTPException(
                status_code=422,
                detail={"message": str(exc), "report": exc.report},
            ) from exc
        except WorkflowError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.post("/projects/{project_id}/queue/drain")
    def drain_queue(project_id: str, request: QueueDrainRequest) -> dict:
        try:
            return service.drain_queue(
                project_id,
                limit=request.limit,
                actor=request.actor,
            )
        except ProjectNotFound as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except WorkflowError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.post("/queue/drain-all")
    def drain_all_queues(request: QueueDrainAllRequest) -> dict:
        try:
            return service.drain_all_queues(
                limit=request.limit,
                actor=request.actor,
                include_archived=request.include_archived,
            )
        except WorkflowError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.post("/jobs/recover-stale")
    def recover_stale_jobs(request: StaleJobRecoveryRequest) -> dict:
        try:
            return service.recover_stale_jobs(
                stale_after_seconds=request.stale_after_seconds,
                actor=request.actor,
            )
        except WorkflowError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.post("/projects/{project_id}/shots/{shot_id}/review-quality")
    def recheck_shot_quality(project_id: str, shot_id: str, request: ActorRequest) -> dict:
        try:
            return service.recheck_shot_quality(project_id, shot_id, actor=request.actor)
        except (ProjectNotFound, ShotNotFound) as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except WorkflowError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.post("/projects/{project_id}/shots/{shot_id}/review")
    def review_shot(
        project_id: str,
        shot_id: str,
        request: ReviewRequest,
    ) -> dict:
        try:
            return service.review_shot(
                project_id,
                shot_id,
                status=ReviewStatus(request.status),
                comment=request.comment,
                actor=request.actor,
            )
        except (ProjectNotFound, ShotNotFound) as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except WorkflowError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.post("/projects/{project_id}/shots/approve-ready")
    def approve_ready_shots(
        project_id: str,
        request: BatchApprovalRequest,
    ) -> dict:
        try:
            return service.approve_ready_shots(
                project_id,
                comment=request.comment,
                actor=request.actor,
            )
        except ProjectNotFound as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except WorkflowError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.post("/projects/{project_id}/shots/{shot_id}/revise")
    def revise_shot(
        project_id: str,
        shot_id: str,
        request: RevisionRequest,
    ) -> dict:
        try:
            return service.revise_shot(
                project_id,
                shot_id,
                comment=request.comment,
            )
        except (ProjectNotFound, ShotNotFound) as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except WorkflowError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.post("/projects/{project_id}/export")
    def export_project(project_id: str) -> dict:
        try:
            return service.export_project(project_id)
        except ProjectNotFound as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except WorkflowError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.post("/projects/{project_id}/package")
    def package_project(project_id: str) -> dict:
        try:
            return service.build_delivery_package(project_id)
        except ProjectNotFound as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except WorkflowError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.post("/projects/{project_id}/package/verify")
    def verify_package(project_id: str, request: ActorRequest) -> dict:
        try:
            return service.verify_delivery_package(
                project_id,
                actor=request.actor,
            )
        except ProjectNotFound as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except WorkflowError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.post("/projects/{project_id}/release")
    def release_project(project_id: str, request: ReleaseRequest) -> dict:
        try:
            return service.release_project(
                project_id,
                channel=request.channel,
                comment=request.comment,
                actor=request.actor,
            )
        except ProjectNotFound as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except WorkflowError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.post("/projects/{project_id}/deliveries/dispatch")
    def dispatch_delivery(
        project_id: str,
        request: DeliveryDispatchRequest,
    ) -> dict:
        try:
            return service.dispatch_delivery(
                project_id,
                channel=request.channel,
                recipient=request.recipient,
                destination_uri=request.destination_uri,
                note=request.note,
                actor=request.actor,
            )
        except ProjectNotFound as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except WorkflowError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.post("/projects/{project_id}/deliveries")
    def record_delivery(project_id: str, request: DeliveryRequest) -> dict:
        try:
            return service.record_delivery(
                project_id,
                channel=request.channel,
                recipient=request.recipient,
                destination_uri=request.destination_uri,
                note=request.note,
                actor=request.actor,
            )
        except ProjectNotFound as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except WorkflowError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.post("/projects/{project_id}/deliveries/{delivery_id}/acknowledge")
    def acknowledge_delivery(
        project_id: str,
        delivery_id: str,
        request: DeliveryAcknowledgeRequest,
    ) -> dict:
        try:
            return service.acknowledge_delivery(
                project_id,
                delivery_id,
                accepted=request.accepted,
                note=request.note,
                actor=request.actor,
            )
        except ProjectNotFound as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except DeliveryNotFound as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except WorkflowError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.get("/projects/{project_id}/delivery-feedback")
    def get_delivery_feedback(
        project_id: str,
        delivery_id: str | None = Query(default=None, max_length=160),
    ) -> dict:
        try:
            return service.delivery_feedback_report(project_id, delivery_id=delivery_id)
        except ProjectNotFound as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except DeliveryNotFound as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post("/projects/{project_id}/delivery-feedback", status_code=status.HTTP_201_CREATED)
    def create_delivery_feedback(
        project_id: str,
        request: DeliveryFeedbackRequest,
    ) -> dict:
        try:
            return service.submit_delivery_feedback(
                project_id,
                delivery_id=request.delivery_id,
                target_type=request.target_type,
                target_id=request.target_id,
                category=request.category,
                severity=request.severity,
                verdict=request.verdict,
                comment=request.comment,
                rating=request.rating,
                assignee=request.assignee,
                actor=request.actor,
            )
        except ProjectNotFound as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except DeliveryNotFound as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except (WorkflowError, ShotNotFound) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.patch("/projects/{project_id}/delivery-feedback/{feedback_id}")
    def triage_delivery_feedback(
        project_id: str,
        feedback_id: str,
        request: DeliveryFeedbackTriageRequest,
    ) -> dict:
        try:
            return service.triage_delivery_feedback(
                project_id,
                feedback_id,
                status=request.status,
                resolution=request.resolution,
                assignee=request.assignee,
                actor=request.actor,
            )
        except ProjectNotFound as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except WorkflowError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.post("/projects/{project_id}/closeout")
    def closeout_project(project_id: str, request: ActorRequest) -> dict:
        try:
            return service.closeout_project(project_id, actor=request.actor)
        except ProjectNotFound as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except WorkflowError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.get("/projects/{project_id}/acceptance")
    def get_project_acceptance(project_id: str) -> dict:
        try:
            return service.project_acceptance(project_id)
        except ProjectNotFound as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except WorkflowError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.post("/projects/{project_id}/acceptance/export")
    def export_project_acceptance(project_id: str, request: ActorRequest) -> dict:
        try:
            return service.export_project_acceptance(
                project_id,
                actor=request.actor,
            )
        except ProjectNotFound as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except WorkflowError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.post("/projects/{project_id}/archive-package")
    def build_archive_package(project_id: str, request: ActorRequest) -> dict:
        try:
            return service.build_archive_package(project_id, actor=request.actor)
        except ProjectNotFound as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except WorkflowError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.post("/projects/{project_id}/archive-package/verify")
    def verify_archive_package(project_id: str, request: ActorRequest) -> dict:
        try:
            return service.verify_archive_package(project_id, actor=request.actor)
        except ProjectNotFound as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except WorkflowError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.api_route(
        "/projects/{project_id}/media/{media_path:path}",
        methods=["GET", "HEAD"],
    )
    def get_project_media(project_id: str, media_path: str) -> FileResponse:
        try:
            service.project_view(project_id)
        except ProjectNotFound as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

        project_root = (service.output_root / project_id).resolve()
        target = (project_root / media_path).resolve()
        if project_root not in target.parents or not target.is_file():
            raise HTTPException(status_code=404, detail="media not found")
        return FileResponse(target)

    static_dir = Path(__file__).parent / "static"
    app.mount("/", StaticFiles(directory=static_dir, html=True), name="studio")

    return app


app = create_app()
