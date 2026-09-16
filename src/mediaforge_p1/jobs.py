from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from .contracts import Artifact, GenerationSpec, JobStatus


class InvalidTransition(ValueError):
    """Raised when a job attempts an illegal state transition."""


@dataclass(frozen=True)
class RetryPolicy:
    """Bounded exponential backoff for provider failures."""

    max_attempts: int = 3
    base_delay_seconds: float = 5.0

    def __post_init__(self) -> None:
        if self.max_attempts < 1:
            raise ValueError("retry max_attempts must be >= 1")
        if self.base_delay_seconds < 0:
            raise ValueError("retry base_delay_seconds must be >= 0")

    @classmethod
    def from_env(cls) -> "RetryPolicy":
        max_attempts_raw = os.getenv("MEDIAFORGE_RETRY_MAX_ATTEMPTS", "3").strip()
        delay_raw = os.getenv(
            "MEDIAFORGE_RETRY_BASE_DELAY_SECONDS",
            "5",
        ).strip()
        try:
            max_attempts = int(max_attempts_raw)
        except ValueError as exc:
            raise ValueError(
                "MEDIAFORGE_RETRY_MAX_ATTEMPTS must be an integer"
            ) from exc
        try:
            base_delay_seconds = float(delay_raw)
        except ValueError as exc:
            raise ValueError(
                "MEDIAFORGE_RETRY_BASE_DELAY_SECONDS must be a number"
            ) from exc
        return cls(
            max_attempts=max_attempts,
            base_delay_seconds=base_delay_seconds,
        )

    def delay_for_attempt(self, attempts: int) -> float:
        exponent = max(attempts - 1, 0)
        return self.base_delay_seconds * (2**exponent)


@dataclass(frozen=True)
class JobLeasePolicy:
    """Controls when an interrupted provider execution may be recovered."""

    stale_after_seconds: float = 900.0

    def __post_init__(self) -> None:
        if self.stale_after_seconds <= 0:
            raise ValueError("job lease stale_after_seconds must be > 0")

    @classmethod
    def from_env(cls) -> "JobLeasePolicy":
        raw = os.getenv("MEDIAFORGE_JOB_LEASE_SECONDS", "900").strip()
        try:
            value = float(raw)
        except ValueError as exc:
            raise ValueError(
                "MEDIAFORGE_JOB_LEASE_SECONDS must be a number"
            ) from exc
        return cls(stale_after_seconds=value)


ALLOWED_TRANSITIONS: dict[JobStatus, set[JobStatus]] = {
    JobStatus.CREATED: {JobStatus.VALIDATED, JobStatus.CANCELED},
    JobStatus.VALIDATED: {JobStatus.QUEUED, JobStatus.CANCELED},
    JobStatus.QUEUED: {JobStatus.ADMITTED, JobStatus.CANCELED},
    JobStatus.ADMITTED: {
        JobStatus.RUNNING,
        JobStatus.FAILED,
        JobStatus.CANCELED,
    },
    JobStatus.RUNNING: {
        JobStatus.SUCCEEDED,
        JobStatus.QUALITY_REJECTED,
        JobStatus.RETRY_WAIT,
        JobStatus.FAILED,
        JobStatus.CANCELED,
    },
    JobStatus.QUALITY_REJECTED: {
        JobStatus.RETRY_WAIT,
        JobStatus.CANCELED,
    },
    JobStatus.RETRY_WAIT: {JobStatus.QUEUED, JobStatus.CANCELED},
    JobStatus.FAILED: {
        JobStatus.RETRY_WAIT,
        JobStatus.CANCELED,
    },
    JobStatus.SUCCEEDED: set(),
    JobStatus.CANCELED: set(),
}


@dataclass(frozen=True)
class JobEvent:
    status: JobStatus
    occurred_at: datetime
    reason: str | None = None


@dataclass
class JobRecord:
    job_id: str
    idempotency_key: str
    spec: GenerationSpec
    trace_id: str | None = None
    status: JobStatus = JobStatus.CREATED
    attempts: int = 0
    max_attempts: int = 3
    retry_at: datetime | None = None
    last_error: str | None = None
    worker_id: str | None = None
    lease_expires_at: datetime | None = None
    last_heartbeat_at: datetime | None = None
    artifacts: list[Artifact] = field(default_factory=list)
    events: list[JobEvent] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.max_attempts < 1:
            raise ValueError("job max_attempts must be >= 1")
        if not self.trace_id:
            self.trace_id = f"trace_job_{self.job_id}"
        self.events.append(
            JobEvent(status=self.status, occurred_at=datetime.now(timezone.utc))
        )

    def transition(self, next_status: JobStatus, reason: str | None = None) -> None:
        if next_status not in ALLOWED_TRANSITIONS[self.status]:
            raise InvalidTransition(f"{self.status} -> {next_status} is not allowed")
        self.status = next_status
        if next_status == JobStatus.RUNNING:
            self.attempts += 1
            self.retry_at = None
        elif next_status in {
            JobStatus.FAILED,
            JobStatus.QUALITY_REJECTED,
        }:
            self.retry_at = None
            self.last_error = reason
        elif next_status == JobStatus.SUCCEEDED:
            self.retry_at = None
            self.last_error = None
        elif next_status == JobStatus.CANCELED:
            self.retry_at = None
        self.events.append(
            JobEvent(
                status=next_status,
                occurred_at=datetime.now(timezone.utc),
                reason=reason,
            )
        )

    def add_artifact(self, artifact: Artifact) -> None:
        if artifact.job_id != self.job_id:
            raise ValueError("artifact belongs to a different job")
        self.artifacts.append(artifact)

    def as_dict(self) -> dict:
        return {
            "job_id": self.job_id,
            "idempotency_key": self.idempotency_key,
            "spec": self.spec.model_dump(mode="json"),
            "trace_id": self.trace_id,
            "status": self.status,
            "attempts": self.attempts,
            "max_attempts": self.max_attempts,
            "retry_at": self.retry_at.isoformat() if self.retry_at else None,
            "last_error": self.last_error,
            "worker_id": self.worker_id,
            "lease_expires_at": (
                self.lease_expires_at.isoformat()
                if self.lease_expires_at
                else None
            ),
            "last_heartbeat_at": (
                self.last_heartbeat_at.isoformat()
                if self.last_heartbeat_at
                else None
            ),
            "artifacts": [
                artifact.model_dump(mode="json")
                for artifact in self.artifacts
            ],
            "events": [
                {
                    "status": event.status,
                    "occurred_at": event.occurred_at.isoformat(),
                    "reason": event.reason,
                }
                for event in self.events
            ],
        }


class JobStore:
    """Small in-memory store used to prove idempotency and state guards."""

    def __init__(self, *, max_attempts: int = 3) -> None:
        if max_attempts < 1:
            raise ValueError("max_attempts must be >= 1")
        self.max_attempts = max_attempts
        self._jobs: dict[str, JobRecord] = {}
        self._by_idempotency_key: dict[str, str] = {}

    def create(
        self,
        spec: GenerationSpec,
        idempotency_key: str,
        *,
        trace_id: str | None = None,
    ) -> JobRecord:
        existing_job_id = self._by_idempotency_key.get(idempotency_key)
        if existing_job_id is not None:
            return self._jobs[existing_job_id]

        job = JobRecord(
            job_id=f"job_{uuid4().hex[:12]}",
            idempotency_key=idempotency_key,
            spec=spec,
            trace_id=trace_id,
            max_attempts=self.max_attempts,
        )
        self._jobs[job.job_id] = job
        self._by_idempotency_key[idempotency_key] = job.job_id
        return job

    def get(self, job_id: str) -> JobRecord:
        try:
            return self._jobs[job_id]
        except KeyError as exc:
            raise KeyError(f"unknown job: {job_id}") from exc

    def transition(
        self,
        job_id: str,
        next_status: JobStatus,
        reason: str | None = None,
    ) -> JobRecord:
        job = self.get(job_id)
        job.transition(next_status, reason)
        return job

    def all(self) -> list[JobRecord]:
        return list(self._jobs.values())

    def schedule_retry(
        self,
        job_id: str,
        *,
        retry_at: datetime,
        reason: str,
    ) -> JobRecord:
        job = self.get(job_id)
        if job.status not in {
            JobStatus.FAILED,
            JobStatus.QUALITY_REJECTED,
            JobStatus.RETRY_WAIT,
        }:
            raise InvalidTransition(
                f"{job.status} -> {JobStatus.RETRY_WAIT} is not allowed"
            )
        if job.attempts >= job.max_attempts:
            raise ValueError(
                f"retry limit reached: {job.attempts}/{job.max_attempts}"
            )
        if job.status in {
            JobStatus.FAILED,
            JobStatus.QUALITY_REJECTED,
        }:
            job.transition(JobStatus.RETRY_WAIT, reason=reason)
        job.retry_at = retry_at
        job.last_error = job.last_error or reason
        return job

    def due_retries(
        self,
        *,
        now: datetime | None = None,
        limit: int | None = None,
    ) -> list[JobRecord]:
        current_time = now or datetime.now(timezone.utc)
        jobs = [
            job
            for job in self._jobs.values()
            if (
                job.status in {
                    JobStatus.FAILED,
                    JobStatus.QUALITY_REJECTED,
                    JobStatus.RETRY_WAIT,
                }
                and job.retry_at is not None
                and job.retry_at <= current_time
            )
        ]
        jobs.sort(key=lambda job: job.retry_at or current_time)
        return jobs[:limit] if limit is not None else jobs

    def promote_retry(self, job_id: str, *, reason: str) -> JobRecord:
        job = self.get(job_id)
        if job.status in {
            JobStatus.FAILED,
            JobStatus.QUALITY_REJECTED,
        }:
            job.transition(JobStatus.RETRY_WAIT, reason=reason)
        if job.status != JobStatus.RETRY_WAIT:
            raise InvalidTransition(
                f"{job.status} -> {JobStatus.QUEUED} is not allowed"
            )
        job.transition(JobStatus.QUEUED, reason=reason)
        job.retry_at = None
        return job

    def as_list(self) -> list[dict]:
        return [job.as_dict() for job in self._jobs.values()]

    def restore(self, records: list[dict]) -> None:
        for record in records:
            job = JobRecord(
                job_id=record["job_id"],
                idempotency_key=record["idempotency_key"],
                spec=GenerationSpec.model_validate(record["spec"]),
                trace_id=record.get("trace_id"),
                status=JobStatus(record["status"]),
                attempts=int(record.get("attempts", 0)),
                max_attempts=int(record.get("max_attempts", self.max_attempts)),
                retry_at=(
                    datetime.fromisoformat(record["retry_at"])
                    if record.get("retry_at")
                    else None
                ),
                last_error=record.get("last_error"),
                worker_id=record.get("worker_id"),
                lease_expires_at=(
                    datetime.fromisoformat(record["lease_expires_at"])
                    if record.get("lease_expires_at")
                    else None
                ),
                last_heartbeat_at=(
                    datetime.fromisoformat(record["last_heartbeat_at"])
                    if record.get("last_heartbeat_at")
                    else None
                ),
                artifacts=[
                    Artifact.model_validate(artifact)
                    for artifact in record.get("artifacts", [])
                ],
                events=[],
            )
            job.events = [
                JobEvent(
                    status=JobStatus(event["status"]),
                    occurred_at=datetime.fromisoformat(event["occurred_at"]),
                    reason=event.get("reason"),
                )
                for event in record.get("events", [])
            ]
            self._jobs[job.job_id] = job
            self._by_idempotency_key[job.idempotency_key] = job.job_id
