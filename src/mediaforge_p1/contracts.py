from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class Capability(StrEnum):
    IMAGE_GENERATION = "image_generation"
    IMAGE_TO_VIDEO = "image_to_video"


class JobStatus(StrEnum):
    CREATED = "CREATED"
    VALIDATED = "VALIDATED"
    QUEUED = "QUEUED"
    ADMITTED = "ADMITTED"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    QUALITY_REJECTED = "QUALITY_REJECTED"
    RETRY_WAIT = "RETRY_WAIT"
    FAILED = "FAILED"
    CANCELED = "CANCELED"


class ProjectStatus(StrEnum):
    DRAFT = "DRAFT"
    PLANNED = "PLANNED"
    IN_PROGRESS = "IN_PROGRESS"
    EXPORTED = "EXPORTED"


class ReviewStatus(StrEnum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    CHANGES_REQUESTED = "CHANGES_REQUESTED"


class NarrativeImportance(StrEnum):
    MAINLINE = "MAINLINE"
    SUPPORTING = "SUPPORTING"
    TRANSITION = "TRANSITION"


class NarrativeCandidateStatus(StrEnum):
    PENDING = "PENDING"
    ADOPTED = "ADOPTED"
    DISCARDED = "DISCARDED"
    STALE = "STALE"


class NarrativeSourceChapterInput(BaseModel):
    """A versioned local chapter imported from a source work."""

    model_config = ConfigDict(extra="forbid")

    chapter_id: str | None = Field(
        default=None,
        min_length=1,
        max_length=120,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]*$",
    )
    source_name: str = Field(min_length=1, max_length=240)
    rights_basis: str = Field(default="project-owned", min_length=1, max_length=500)
    allow_external_processing: bool = False
    chapter_number: int = Field(ge=1, le=100_000)
    title: str = Field(min_length=1, max_length=240)
    content: str = Field(min_length=1, max_length=120_000)


class NarrativeSourceDocument(BaseModel):
    model_config = ConfigDict(extra="forbid")

    document_id: str = Field(
        min_length=1,
        max_length=120,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]*$",
    )
    name: str = Field(min_length=1, max_length=240)
    format: Literal["txt", "md", "docx", "pdf"]
    source_name: str = Field(min_length=1, max_length=240)
    rights_basis: str = Field(min_length=1, max_length=500)
    allow_external_processing: bool = False
    sha256: str = Field(min_length=64, max_length=64)
    size_bytes: int = Field(gt=0, le=20 * 1024 * 1024)
    parser_version: str = Field(min_length=1, max_length=120)
    extraction_method: Literal["native_text", "ocr_command", "ocr_http"] = "native_text"
    ocr_processor: Literal["command", "http"] | None = None
    chapter_ids: list[str] = Field(default_factory=list, max_length=500)
    uri: str = Field(min_length=1, max_length=4_000)
    imported_at: datetime


class NarrativeSourceChapter(NarrativeSourceChapterInput):
    chapter_id: str = Field(
        min_length=1,
        max_length=120,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]*$",
    )
    revision: int = Field(default=1, ge=1)
    content_sha256: str = Field(min_length=64, max_length=64)
    source_document_id: str | None = Field(default=None, max_length=120)
    source_document_name: str | None = Field(default=None, max_length=240)
    source_document_sha256: str | None = Field(default=None, min_length=64, max_length=64)
    source_document_format: Literal["txt", "md", "docx", "pdf"] | None = None
    source_document_locator: str | None = Field(default=None, max_length=500)
    created_at: datetime
    updated_at: datetime


class NarrativeEventCandidateProposal(BaseModel):
    """An unapproved event extracted from a source chapter."""

    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=240)
    scene: str = Field(min_length=1, max_length=240)
    summary: str = Field(min_length=1, max_length=2_000)
    characters: list[str] = Field(default_factory=list, max_length=4)
    importance: NarrativeImportance = NarrativeImportance.MAINLINE
    emotions: list[str] = Field(default_factory=list, max_length=6)
    estimated_duration_seconds: int = Field(default=5, ge=1, le=60)
    source_locator: str | None = Field(default=None, max_length=500)
    source_excerpt: str | None = Field(default=None, max_length=8_000)


class NarrativeEventCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    candidate_id: str = Field(
        min_length=1,
        max_length=120,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]*$",
    )
    source_chapter_id: str = Field(min_length=1, max_length=120)
    source_chapter_revision: int = Field(ge=1)
    source_content_sha256: str = Field(min_length=64, max_length=64)
    sequence: int = Field(ge=1, le=10_000)
    proposal: NarrativeEventCandidateProposal
    extractor: str = Field(min_length=1, max_length=160)
    extraction_model: str | None = Field(default=None, max_length=240)
    status: NarrativeCandidateStatus = NarrativeCandidateStatus.PENDING
    revision: int = Field(default=1, ge=1)
    adopted_event_id: str | None = Field(default=None, max_length=120)
    discarded_reason: str | None = Field(default=None, max_length=500)
    created_at: datetime
    updated_at: datetime


class NarrativeEventInput(BaseModel):
    """A reviewed story beat with a stable link back to its source material."""

    model_config = ConfigDict(extra="forbid")

    event_id: str | None = Field(
        default=None,
        min_length=1,
        max_length=120,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]*$",
    )
    chapter_number: int = Field(ge=1, le=100_000)
    sequence: int = Field(ge=1, le=10_000)
    title: str = Field(min_length=1, max_length=240)
    scene: str = Field(min_length=1, max_length=240)
    summary: str = Field(min_length=1, max_length=2_000)
    characters: list[str] = Field(min_length=1, max_length=4)
    importance: NarrativeImportance = NarrativeImportance.MAINLINE
    emotions: list[str] = Field(default_factory=list, max_length=6)
    estimated_duration_seconds: int = Field(default=5, ge=1, le=60)
    source_chapter_id: str | None = Field(default=None, max_length=120)
    source_locator: str | None = Field(default=None, max_length=500)
    source_excerpt: str | None = Field(default=None, max_length=8_000)


class NarrativeEvent(NarrativeEventInput):
    event_id: str = Field(
        min_length=1,
        max_length=120,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]*$",
    )
    revision: int = Field(default=1, ge=1)
    review_status: ReviewStatus = ReviewStatus.PENDING
    source_sha256: str = Field(min_length=64, max_length=64)
    source_chapter_content_sha256: str | None = Field(default=None, min_length=64, max_length=64)
    created_at: datetime
    updated_at: datetime


class AdaptationSceneInput(BaseModel):
    """A reviewable screenplay scene derived from approved narrative events."""

    model_config = ConfigDict(extra="forbid")

    scene_id: str | None = Field(
        default=None,
        min_length=1,
        max_length=120,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]*$",
    )
    sequence: int = Field(ge=1, le=10_000)
    heading: str = Field(min_length=1, max_length=240)
    synopsis: str = Field(min_length=1, max_length=4_000)
    beats: list[str] = Field(default_factory=list, max_length=12)
    dialogue_draft: str = Field(default="", max_length=8_000)
    characters: list[str] = Field(min_length=1, max_length=4)
    mood: str = Field(default="", max_length=120)
    estimated_duration_seconds: int = Field(default=5, ge=1, le=60)
    source_event_ids: list[str] = Field(min_length=1, max_length=12)


class AdaptationScene(AdaptationSceneInput):
    scene_id: str = Field(
        min_length=1,
        max_length=120,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]*$",
    )
    revision: int = Field(default=1, ge=1)
    review_status: ReviewStatus = ReviewStatus.PENDING
    source_events_sha256: str = Field(min_length=64, max_length=64)
    derived_from: str = Field(default="manual", min_length=1, max_length=120)
    created_at: datetime
    updated_at: datetime


class CreativeBrief(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project_id: str = Field(
        min_length=1,
        max_length=64,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]*$",
    )
    tenant_id: str = Field(
        default="default",
        min_length=1,
        max_length=64,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]*$",
    )
    title: str = Field(min_length=1)
    premise: str = Field(min_length=1)
    genre: str = Field(min_length=1)
    style: str = Field(min_length=1)
    duration_seconds: int = Field(ge=30, le=60)
    budget: float = Field(gt=0)
    characters: list[str] = Field(min_length=2, max_length=4)


class ShotCard(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project_id: str = Field(min_length=1)
    shot_id: str = Field(min_length=1)
    scene: str = Field(min_length=1)
    description: str = Field(min_length=1)
    characters: list[str] = Field(min_length=1)
    duration_seconds: int = Field(ge=1, le=5)
    mood: str = Field(min_length=1)
    subtitle_text: str | None = Field(default=None, max_length=500)


class StoryCharacter(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=120)
    role: str = Field(min_length=1, max_length=500)
    constraints: list[str] = Field(default_factory=list, max_length=20)


class StoryScene(BaseModel):
    model_config = ConfigDict(extra="forbid")
    scene: str = Field(min_length=1, max_length=200)
    summary: str = Field(min_length=1, max_length=2000)


class ScriptPackage(BaseModel):
    model_config = ConfigDict(extra="forbid")
    theme: str = Field(min_length=1, max_length=1000)
    logline: str = Field(min_length=1, max_length=2000)
    characters: list[StoryCharacter] = Field(min_length=2, max_length=4)
    scenes: list[StoryScene] = Field(min_length=1, max_length=12)


class DialogueLine(BaseModel):
    """A timed, speaker-specific line in the project dialogue track."""

    model_config = ConfigDict(extra="forbid")

    line_id: str = Field(min_length=1, max_length=120, pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]*$")
    shot_id: str = Field(min_length=1, max_length=160)
    speaker: str = Field(min_length=1, max_length=120)
    text: str = Field(min_length=1, max_length=1200)
    start_seconds: float = Field(ge=0)
    end_seconds: float = Field(gt=0)
    voice: str = Field(default="default", min_length=1, max_length=120)
    language: str = Field(default="zh-CN", min_length=2, max_length=20)


class Intent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    shot_type: str = Field(min_length=1)
    camera_motion: str = Field(min_length=1)
    duration_seconds: float = Field(ge=1, le=10)
    mood: str = Field(min_length=1)


class ProviderConstraints(BaseModel):
    model_config = ConfigDict(extra="forbid")

    capability: Capability
    resolution: Literal["512p", "720p", "1080p"] = "720p"
    max_cost: float = Field(gt=0)
    deadline_seconds: int = Field(gt=0)


class ControlNet(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    strength: float = Field(ge=0, le=1)


class WorkflowSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    template_id: str = Field(min_length=1)
    allowed_lora_ids: list[str] = Field(default_factory=list)
    controlnet: ControlNet


class QualityRequirements(BaseModel):
    model_config = ConfigDict(extra="forbid")

    minimum_character_similarity: float = Field(ge=0, le=1)
    must_not_include: list[str] = Field(default_factory=list)


class ReferenceAssetRef(BaseModel):
    """A versioned reference asset that a provider may use as generation input."""

    model_config = ConfigDict(extra="forbid")

    asset_id: str = Field(min_length=1, max_length=240)
    name: str = Field(min_length=1, max_length=200)
    version: str = Field(min_length=1, max_length=40)
    kind: Literal[
        "character_reference",
        "style_reference",
        "location_reference",
    ] = "character_reference"
    character: str | None = Field(default=None, max_length=120)
    uri: str | None = Field(default=None, max_length=2000)
    mime_type: str | None = Field(default=None, max_length=120)
    sha256: str | None = Field(default=None, min_length=64, max_length=64)
    size_bytes: int | None = Field(default=None, ge=1)
    license: str = Field(min_length=1, max_length=120)
    source: str = Field(min_length=1, max_length=120)


class GenerationSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project_id: str = Field(min_length=1)
    shot_id: str = Field(min_length=1)
    asset_versions: dict[str, str] = Field(min_length=1)
    reference_assets: list[ReferenceAssetRef] = Field(default_factory=list)
    intent: Intent
    provider_constraints: ProviderConstraints
    workflow: WorkflowSpec
    quality_requirements: QualityRequirements


class Artifact(BaseModel):
    model_config = ConfigDict(extra="forbid")

    artifact_id: str = Field(min_length=1)
    job_id: str = Field(min_length=1)
    kind: Literal["image", "video"]
    uri: str = Field(min_length=1)
    mime_type: str = Field(min_length=1)
    sha256: str = Field(min_length=64, max_length=64)
    size_bytes: int = Field(ge=1)
    duration_seconds: float | None = Field(default=None, ge=0)
    metadata_uri: str | None = None
    created_at: datetime


class QualityReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    passed: bool
    checks: dict[str, bool]
    reasons: list[str] = Field(default_factory=list)
    created_at: datetime
