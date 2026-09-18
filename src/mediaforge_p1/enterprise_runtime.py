from __future__ import annotations

import hashlib
import json
import math
import os
import sqlite3
import threading
import time
from collections import defaultdict, deque
from dataclasses import dataclass
from math import ceil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from uuid import uuid4

from .auth import AuthManager


class EnterpriseConfigurationError(ValueError):
    pass


class ControlPlaneUnavailable(EnterpriseConfigurationError):
    """Raised when this API instance is not the current control-plane writer."""


@dataclass(frozen=True)
class RateLimitDecision:
    """One atomic rate-limit check with a client-safe recovery deadline."""

    allowed: bool
    remaining: int
    retry_after_seconds: int | None = None
    reset_at_epoch: int | None = None


class SlidingWindowRateLimiter:
    """Process-local limiter with a durable SQLite backend available to every API worker."""

    def __init__(self, path: Path, *, limit: int = 120, window_seconds: int = 60) -> None:
        self.path = path
        self.limit = max(int(limit), 1)
        self.window_seconds = max(int(window_seconds), 1)
        self._lock = threading.RLock()
        self._windows: dict[str, deque[float]] = defaultdict(deque)

    @classmethod
    def from_env(
        cls,
        output_root: Path,
        *,
        limit_env: str = "MEDIAFORGE_RATE_LIMIT_REQUESTS",
        default_limit: int = 120,
    ) -> "SlidingWindowRateLimiter":
        try:
            limit = int(os.getenv(limit_env, str(default_limit)))
            window = int(os.getenv("MEDIAFORGE_RATE_LIMIT_WINDOW_SECONDS", "60"))
        except ValueError as exc:
            raise EnterpriseConfigurationError("rate limit settings must be integers") from exc
        path = Path(os.getenv("MEDIAFORGE_RATE_LIMIT_DB", str(output_root / "rate-limit.sqlite3")))
        return cls(path, limit=limit, window_seconds=window)

    @property
    def enabled(self) -> bool:
        return os.getenv("MEDIAFORGE_RATE_LIMIT_ENABLED", "true").strip().lower() not in {"0", "false", "no"}

    def allow(self, key: str, *, now: float | None = None) -> tuple[bool, int]:
        decision = self.check(key, now=now)
        return decision.allowed, decision.remaining

    def check(self, key: str, *, now: float | None = None) -> RateLimitDecision:
        if not self.enabled:
            return RateLimitDecision(allowed=True, remaining=self.limit)
        current = time.time() if now is None else now
        with self._lock:
            if os.getenv("MEDIAFORGE_RATE_LIMIT_BACKEND", "sqlite").strip().lower() == "sqlite":
                return self._check_sqlite(key, current)
            bucket = self._windows[key]
            cutoff = current - self.window_seconds
            while bucket and bucket[0] <= cutoff:
                bucket.popleft()
            if len(bucket) >= self.limit:
                reset_at = bucket[0] + self.window_seconds
                return RateLimitDecision(
                    allowed=False,
                    remaining=0,
                    retry_after_seconds=max(1, ceil(reset_at - current)),
                    reset_at_epoch=ceil(reset_at),
                )
            bucket.append(current)
            reset_at = bucket[0] + self.window_seconds
            return RateLimitDecision(
                allowed=True,
                remaining=self.limit - len(bucket),
                reset_at_epoch=ceil(reset_at),
            )

    def status_view(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "backend": os.getenv("MEDIAFORGE_RATE_LIMIT_BACKEND", "sqlite"),
            "limit": self.limit,
            "window_seconds": self.window_seconds,
            "path": str(self.path),
        }

    def _check_sqlite(self, key: str, current: float) -> RateLimitDecision:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.path, timeout=5, isolation_level=None) as connection:
            # Serialize the read-check-write sequence across API processes.
            connection.execute("BEGIN IMMEDIATE")
            connection.execute("CREATE TABLE IF NOT EXISTS rate_window (bucket TEXT NOT NULL, occurred_at REAL NOT NULL)")
            connection.execute("CREATE INDEX IF NOT EXISTS rate_window_bucket_time ON rate_window(bucket, occurred_at)")
            cutoff = current - self.window_seconds
            connection.execute("DELETE FROM rate_window WHERE occurred_at <= ?", (cutoff,))
            count, oldest = connection.execute(
                "SELECT COUNT(*), MIN(occurred_at) FROM rate_window WHERE bucket = ?",
                (key,),
            ).fetchone()
            count = int(count)
            if count >= self.limit:
                reset_at = float(oldest) + self.window_seconds
                connection.rollback()
                return RateLimitDecision(
                    allowed=False,
                    remaining=0,
                    retry_after_seconds=max(1, ceil(reset_at - current)),
                    reset_at_epoch=ceil(reset_at),
                )
            connection.execute("INSERT INTO rate_window(bucket, occurred_at) VALUES (?, ?)", (key, current))
            connection.commit()
            reset_at = (float(oldest) if oldest is not None else current) + self.window_seconds
            return RateLimitDecision(
                allowed=True,
                remaining=self.limit - count - 1,
                reset_at_epoch=ceil(reset_at),
            )


class BillingLedger:
    """Idempotent usage and settlement ledger for quotas and external reconciliation."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.RLock()
        self._ensure_schema()

    @classmethod
    def from_env(cls, output_root: Path) -> "BillingLedger":
        return cls(Path(os.getenv("MEDIAFORGE_BILLING_DB", str(output_root / "billing.sqlite3"))))

    def record(self, *, event_id: str, tenant_id: str, category: str, quantity: float, unit_price: float, project_id: str | None = None, currency: str = "USD", metadata: dict[str, Any] | None = None) -> dict[str, Any]:
        if not event_id.strip() or not tenant_id.strip() or not category.strip():
            raise EnterpriseConfigurationError("event_id, tenant_id and category are required")
        if not math.isfinite(quantity) or not math.isfinite(unit_price) or quantity < 0 or unit_price < 0 or not math.isfinite(quantity * unit_price):
            raise EnterpriseConfigurationError("quantity and unit_price must be finite and non-negative")
        if len(currency) != 3 or not currency.isascii() or not currency.isalpha():
            raise EnterpriseConfigurationError("currency must contain three ASCII letters")
        payload = json.dumps(metadata or {}, ensure_ascii=True, sort_keys=True)
        with self._lock, sqlite3.connect(self.path, timeout=10) as connection:
            connection.execute("INSERT OR IGNORE INTO usage_event(event_id, tenant_id, project_id, category, quantity, unit_price, currency, occurred_at, metadata) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", (event_id, tenant_id, project_id, category, float(quantity), float(unit_price), currency.upper(), time.time(), payload))
            row = connection.execute("SELECT event_id, tenant_id, project_id, category, quantity, unit_price, currency, occurred_at, metadata FROM usage_event WHERE event_id = ? AND tenant_id = ?", (event_id, tenant_id)).fetchone()
        assert row is not None
        return self._row(row)

    def record_settlement(
        self,
        *,
        settlement_id: str,
        tenant_id: str,
        provider: str,
        status: str,
        amount: float,
        currency: str = "USD",
        external_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        allowed_statuses = {"pending", "paid", "failed", "refunded", "void"}
        if not settlement_id.strip() or not tenant_id.strip() or not provider.strip():
            raise EnterpriseConfigurationError("settlement_id, tenant_id and provider are required")
        if status not in allowed_statuses:
            raise EnterpriseConfigurationError("settlement status is invalid")
        if not math.isfinite(amount) or amount < 0:
            raise EnterpriseConfigurationError("settlement amount must be finite and non-negative")
        if len(currency) != 3 or not currency.isascii() or not currency.isalpha():
            raise EnterpriseConfigurationError("currency must contain three ASCII letters")
        payload = json.dumps(metadata or {}, ensure_ascii=True, sort_keys=True)
        with self._lock, sqlite3.connect(self.path, timeout=10) as connection:
            existing = connection.execute(
                "SELECT settlement_id, tenant_id, provider, status, amount, currency, external_id, occurred_at, metadata FROM settlement WHERE settlement_id = ? AND tenant_id = ?",
                (settlement_id, tenant_id),
            ).fetchone()
            if existing is None:
                connection.execute(
                    "INSERT INTO settlement(settlement_id, tenant_id, provider, status, amount, currency, external_id, occurred_at, metadata) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (settlement_id, tenant_id, provider, status, float(amount), currency.upper(), external_id, time.time(), payload),
                )
            else:
                current = self._settlement_row(existing)
                if current["provider"] != provider or current["amount"] != round(float(amount), 8) or current["currency"] != currency.upper():
                    raise EnterpriseConfigurationError("settlement identity does not match the existing record")
                transitions = {
                    "pending": {"pending", "paid", "failed", "void"},
                    "paid": {"paid", "refunded"},
                    "failed": {"failed"},
                    "refunded": {"refunded"},
                    "void": {"void"},
                }
                if status not in transitions[current["status"]]:
                    raise EnterpriseConfigurationError("settlement status transition is invalid")
                if status != current["status"] or external_id != current["external_id"] or payload != json.dumps(current["metadata"], ensure_ascii=True, sort_keys=True):
                    connection.execute(
                        "UPDATE settlement SET status = ?, external_id = ?, occurred_at = ?, metadata = ? WHERE settlement_id = ? AND tenant_id = ?",
                        (status, external_id, time.time(), payload, settlement_id, tenant_id),
                    )
            row = connection.execute(
                "SELECT settlement_id, tenant_id, provider, status, amount, currency, external_id, occurred_at, metadata FROM settlement WHERE settlement_id = ? AND tenant_id = ?",
                (settlement_id, tenant_id),
            ).fetchone()
        assert row is not None
        return self._settlement_row(row)

    def settlement_summary(self, tenant_id: str | None = None) -> dict[str, Any]:
        query = "SELECT status, tenant_id, amount, currency FROM settlement"
        args: tuple[Any, ...] = ()
        if tenant_id:
            query += " WHERE tenant_id = ?"
            args = (tenant_id,)
        with sqlite3.connect(self.path, timeout=10) as connection:
            rows = connection.execute(query, args).fetchall()
        by_currency: dict[str, dict[str, Any]] = {}
        by_status: dict[str, int] = defaultdict(int)
        for status, _owner, amount, currency in rows:
            by_status[status] += 1
            item = by_currency.setdefault(currency, {"amount": 0.0, "by_status": defaultdict(float)})
            item["amount"] += float(amount)
            item["by_status"][status] += float(amount)
        currencies = {
            currency: {
                "amount": round(item["amount"], 8),
                "by_status": {key: round(value, 8) for key, value in sorted(item["by_status"].items())},
            }
            for currency, item in sorted(by_currency.items())
        }
        return {
            "schema_version": "mediaforge-settlement-summary-v1",
            "settlement_count": len(rows),
            "by_status": dict(sorted(by_status.items())),
            "by_currency": currencies,
        }

    def settlement_page(
        self,
        tenant_id: str | None = None,
        *,
        status: str | None = None,
        currency: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> dict[str, Any]:
        if not 1 <= limit <= 500 or offset < 0:
            raise EnterpriseConfigurationError("invalid settlement pagination")
        if status and status not in {"pending", "paid", "failed", "refunded", "void"}:
            raise EnterpriseConfigurationError("settlement status is invalid")
        clauses, args = [], []
        for column, value in (("tenant_id", tenant_id), ("status", status), ("currency", currency.upper() if currency else None)):
            if value is not None:
                clauses.append(f"{column} = ?")
                args.append(value)
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        with sqlite3.connect(self.path, timeout=10) as connection:
            total = connection.execute("SELECT COUNT(*) FROM settlement" + where, args).fetchone()[0]
            rows = connection.execute(
                "SELECT settlement_id, tenant_id, provider, status, amount, currency, external_id, occurred_at, metadata FROM settlement"
                + where + " ORDER BY occurred_at DESC, tenant_id, settlement_id LIMIT ? OFFSET ?",
                [*args, limit, offset],
            ).fetchall()
        return {
            "settlements": [self._settlement_row(row) for row in rows],
            "total": total,
            "offset": offset,
            "limit": limit,
            "has_more": offset + len(rows) < total,
        }

    def summary(self, tenant_id: str | None = None) -> dict[str, Any]:
        query = "SELECT category, tenant_id, quantity, unit_price, currency FROM usage_event"
        args: tuple[Any, ...] = ()
        if tenant_id:
            query += " WHERE tenant_id = ?"
            args = (tenant_id,)
        with sqlite3.connect(self.path, timeout=10) as connection:
            rows = connection.execute(query, args).fetchall()
        totals: dict[str, Any] = {}
        for category, owner, quantity, unit_price, currency in rows:
            total = totals.setdefault(currency, {"amount": 0.0, "by_category": defaultdict(float), "by_tenant": defaultdict(float)})
            value = float(quantity) * float(unit_price)
            total["amount"] += value
            total["by_category"][category] += value
            total["by_tenant"][owner] += value
        by_currency = {currency: {"amount": round(total["amount"], 8), "by_category": {key: round(value, 8) for key, value in total["by_category"].items()}, "by_tenant": {key: round(value, 8) for key, value in total["by_tenant"].items()}} for currency, total in sorted(totals.items())}
        currency = next(iter(by_currency)) if len(by_currency) == 1 else (None if by_currency else os.getenv("MEDIAFORGE_BILLING_CURRENCY", "USD"))
        combined = by_currency.get(currency, {"amount": None if by_currency else 0.0, "by_category": {}, "by_tenant": {}})
        return {"schema_version": "mediaforge-billing-summary-v1", "event_count": len(rows), "currency": currency, **combined, "by_currency": by_currency}

    def events(self, tenant_id: str | None = None) -> list[dict[str, Any]]:
        query = "SELECT event_id, tenant_id, project_id, category, quantity, unit_price, currency, occurred_at, metadata FROM usage_event"
        args: tuple[Any, ...] = ()
        if tenant_id:
            query += " WHERE tenant_id = ?"
            args = (tenant_id,)
        with sqlite3.connect(self.path, timeout=10) as connection:
            rows = connection.execute(query + " ORDER BY occurred_at", args).fetchall()
        return [self._row(row) for row in rows]

    def status_view(self) -> dict[str, Any]:
        with sqlite3.connect(self.path, timeout=10) as connection:
            count = int(connection.execute("SELECT COUNT(*) FROM usage_event").fetchone()[0])
            settlement_count = int(connection.execute("SELECT COUNT(*) FROM settlement").fetchone()[0])
        return {"configured": True, "backend": "sqlite-ledger", "path": str(self.path), "event_count": count, "settlement_count": settlement_count}

    def event_page(self, tenant_id: str | None = None, *, project_id: str | None = None,
                   category: str | None = None, currency: str | None = None,
                   since: float | None = None, until: float | None = None,
                   limit: int = 50, offset: int = 0) -> dict[str, Any]:
        if not 1 <= limit <= 500 or offset < 0:
            raise EnterpriseConfigurationError("invalid billing pagination")
        if any(value is not None and not math.isfinite(value) for value in (since, until)) or (since is not None and until is not None and since > until):
            raise EnterpriseConfigurationError("invalid billing date range")
        clauses, args = [], []
        for column, value in (("tenant_id", tenant_id), ("project_id", project_id), ("category", category), ("currency", currency.upper() if currency else None)):
            if value is not None:
                clauses.append(f"{column} = ?")
                args.append(value)
        for operator, value in ((">=", since), ("<=", until)):
            if value is not None:
                clauses.append(f"occurred_at {operator} ?")
                args.append(value)
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        with sqlite3.connect(self.path, timeout=10) as connection:
            connection.execute("BEGIN")
            total = connection.execute("SELECT COUNT(*) FROM usage_event" + where, args).fetchone()[0]
            rows = connection.execute("SELECT event_id, tenant_id, project_id, category, quantity, unit_price, currency, occurred_at, metadata FROM usage_event" + where + " ORDER BY occurred_at DESC, tenant_id, event_id LIMIT ? OFFSET ?", [*args, limit, offset]).fetchall()
        return {"events": [self._row(row) for row in rows], "total": total, "offset": offset, "limit": limit, "has_more": offset + len(rows) < total}

    def _ensure_schema(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.path, timeout=10) as connection:
            connection.execute("BEGIN IMMEDIATE")
            primary_key = [row[1] for row in connection.execute("PRAGMA table_info(usage_event)") if row[5]]
            if primary_key == ["event_id"]:
                connection.execute("ALTER TABLE usage_event RENAME TO usage_event_legacy")
            connection.execute("CREATE TABLE IF NOT EXISTS usage_event (event_id TEXT NOT NULL, tenant_id TEXT NOT NULL, project_id TEXT, category TEXT NOT NULL, quantity REAL NOT NULL, unit_price REAL NOT NULL, currency TEXT NOT NULL, occurred_at REAL NOT NULL, metadata TEXT NOT NULL, PRIMARY KEY(tenant_id, event_id))")
            if primary_key == ["event_id"]:
                connection.execute("INSERT INTO usage_event SELECT * FROM usage_event_legacy")
                connection.execute("DROP TABLE usage_event_legacy")
            connection.execute("CREATE INDEX IF NOT EXISTS usage_event_tenant_time ON usage_event(tenant_id, occurred_at)")
            connection.execute("CREATE TABLE IF NOT EXISTS settlement (settlement_id TEXT NOT NULL, tenant_id TEXT NOT NULL, provider TEXT NOT NULL, status TEXT NOT NULL, amount REAL NOT NULL, currency TEXT NOT NULL, external_id TEXT, occurred_at REAL NOT NULL, metadata TEXT NOT NULL, PRIMARY KEY(tenant_id, settlement_id))")
            connection.execute("CREATE INDEX IF NOT EXISTS settlement_tenant_time ON settlement(tenant_id, occurred_at)")

    @staticmethod
    def _row(row: tuple[Any, ...]) -> dict[str, Any]:
        event_id, tenant_id, project_id, category, quantity, unit_price, currency, occurred_at, metadata = row
        return {"event_id": event_id, "tenant_id": tenant_id, "project_id": project_id, "category": category, "quantity": quantity, "unit_price": unit_price, "amount": round(float(quantity) * float(unit_price), 8), "currency": currency, "occurred_at": occurred_at, "metadata": json.loads(metadata)}

    @staticmethod
    def _settlement_row(row: tuple[Any, ...]) -> dict[str, Any]:
        settlement_id, tenant_id, provider, status, amount, currency, external_id, occurred_at, metadata = row
        return {"settlement_id": settlement_id, "tenant_id": tenant_id, "provider": provider, "status": status, "amount": round(float(amount), 8), "currency": currency, "external_id": external_id, "occurred_at": occurred_at, "metadata": json.loads(metadata)}


class ObjectStorage:
    """Filesystem storage with an S3-compatible configuration and safe local acceptance path."""

    def __init__(self, root: Path, *, mode: str = "local", endpoint: str = "", bucket: str = "") -> None:
        self.root = root
        self.mode = mode.strip().lower() or "local"
        self.endpoint = endpoint.strip()
        self.bucket = bucket.strip()
        self.connection_verified = False

    @classmethod
    def from_env(cls, output_root: Path) -> "ObjectStorage":
        return cls(output_root, mode=os.getenv("MEDIAFORGE_STORAGE_MODE", "local"), endpoint=os.getenv("MEDIAFORGE_STORAGE_ENDPOINT", ""), bucket=os.getenv("MEDIAFORGE_STORAGE_BUCKET", ""))

    def put(self, source: Path, key: str) -> dict[str, Any]:
        normalized = self._normalize_key(key)
        if self.mode in {"s3", "minio", "object"}:
            try:
                import boto3
            except ImportError as exc:
                raise EnterpriseConfigurationError("boto3 is required for S3-compatible storage") from exc
            if not self.bucket:
                raise EnterpriseConfigurationError("MEDIAFORGE_STORAGE_BUCKET is required for S3-compatible storage")
            client = boto3.client("s3", endpoint_url=self.endpoint or None)
            try:
                client.upload_file(str(source), self.bucket, normalized)
            except Exception as exc:
                self.connection_verified = False
                raise EnterpriseConfigurationError("object storage upload failed") from exc
            self.connection_verified = True
            return {"key": normalized, "uri": f"s3://{self.bucket}/{normalized}", "sha256": self._sha256(source), "size": source.stat().st_size}
        if self.mode != "local":
            raise EnterpriseConfigurationError(f"unsupported object storage mode: {self.mode}")
        destination = self.root / "objects" / normalized
        if not destination.resolve().is_relative_to((self.root / "objects").resolve()):
            raise EnterpriseConfigurationError("object key escapes storage root")
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(source.read_bytes())
        return {"key": normalized, "uri": destination.as_uri(), "sha256": self._sha256(destination), "size": destination.stat().st_size}

    def put_immutable(
        self,
        source: Path,
        key: str,
        *,
        retention_until: datetime,
        object_lock_mode: str = "COMPLIANCE",
    ) -> dict[str, Any]:
        """Write an S3 object with retention and read the retention back before success."""
        normalized = self._normalize_key(key)
        if self.mode not in {"s3", "minio", "object"}:
            raise EnterpriseConfigurationError("immutable object writes require S3-compatible storage")
        if not self.bucket:
            raise EnterpriseConfigurationError("MEDIAFORGE_STORAGE_BUCKET is required for S3-compatible storage")
        mode = object_lock_mode.strip().upper()
        if mode not in {"COMPLIANCE", "GOVERNANCE"}:
            raise EnterpriseConfigurationError("object lock mode must be COMPLIANCE or GOVERNANCE")
        if retention_until.tzinfo is None:
            raise EnterpriseConfigurationError("object lock retention time must include a timezone")
        try:
            import boto3
        except ImportError as exc:
            raise EnterpriseConfigurationError("boto3 is required for S3-compatible storage") from exc
        client = boto3.client("s3", endpoint_url=self.endpoint or None)
        try:
            result = client.put_object(
                Bucket=self.bucket,
                Key=normalized,
                Body=source.read_bytes(),
                ContentType="application/json",
                ObjectLockMode=mode,
                ObjectLockRetainUntilDate=retention_until,
            )
            retention = client.get_object_retention(Bucket=self.bucket, Key=normalized).get("Retention") or {}
        except Exception as exc:
            self.connection_verified = False
            raise EnterpriseConfigurationError("immutable object storage write or retention verification failed") from exc
        actual_mode = str(retention.get("Mode") or "").upper()
        actual_until = retention.get("RetainUntilDate")
        if not isinstance(actual_until, datetime) or actual_until.tzinfo is None:
            self.connection_verified = False
            raise EnterpriseConfigurationError("object storage returned no verifiable retention timestamp")
        requested_until = retention_until.astimezone(timezone.utc)
        if actual_mode != mode or actual_until.astimezone(timezone.utc) < requested_until:
            self.connection_verified = False
            raise EnterpriseConfigurationError("object storage retention does not match the requested immutable policy")
        self.connection_verified = True
        return {
            "key": normalized,
            "uri": f"s3://{self.bucket}/{normalized}",
            "sha256": self._sha256(source),
            "size": source.stat().st_size,
            "version_id": result.get("VersionId"),
            "object_lock_verified": True,
            "retention_mode": actual_mode,
            "retention_until": actual_until.astimezone(timezone.utc).isoformat(),
        }

    def status_view(self) -> dict[str, Any]:
        parsed = urlparse(self.endpoint) if self.endpoint else None
        remote = self.mode in {"s3", "minio", "object"}
        driver_ready = True
        if remote:
            try:
                import boto3  # noqa: F401
            except ImportError:
                driver_ready = False
        configured = self.mode == "local" or (remote and bool(self.bucket))
        return {"mode": self.mode, "configured": configured, "endpoint": f"{parsed.scheme}://{parsed.hostname}" if parsed and parsed.hostname else None, "bucket": self.bucket or None, "backend": "filesystem" if self.mode == "local" else "s3-compatible", "driver_ready": driver_ready, "write_enabled": configured and driver_ready, "connectivity_verified": self.mode == "local" or self.connection_verified}

    def probe(self) -> None:
        if self.mode == "local":
            return
        self.connection_verified = False
        if self.mode not in {"s3", "minio", "object"} or not self.bucket:
            raise EnterpriseConfigurationError("S3-compatible storage is not configured")
        import boto3
        boto3.client("s3", endpoint_url=self.endpoint or None).head_bucket(Bucket=self.bucket)
        self.connection_verified = True

    @staticmethod
    def _sha256(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    @staticmethod
    def _normalize_key(key: str) -> str:
        normalized = key.replace("\\", "/")
        if not normalized or any(part in {"", ".", ".."} or ":" in part for part in normalized.split("/")):
            raise EnterpriseConfigurationError("invalid object key")
        return normalized


class RedisQueueAdapter:
    """Recoverable ready-job index; durable job state owns Worker leases."""

    def __init__(self, url: str, queue_name: str = "mediaforge:jobs") -> None:
        self.url = url.strip()
        self.queue_name = queue_name.strip() or "mediaforge:jobs"
        self.connection_verified = False

    @property
    def configured(self) -> bool:
        return bool(self.url)

    def enqueue(self, payload: dict[str, Any]) -> None:
        job_id = str(payload.get("job_id") or "")
        if not job_id:
            raise EnterpriseConfigurationError("Redis queue payload requires job_id")
        try:
            self._client().zadd(self.queue_name + ":ready", {job_id: time.time()}, nx=True)
            self.connection_verified = True
        except Exception as exc:
            self.connection_verified = False
            raise EnterpriseConfigurationError("Redis enqueue failed; durable job state is retained") from exc

    def ordered_pending(self, job_ids: list[str]) -> list[str]:
        if not job_ids:
            return []
        try:
            client = self._client()
            # Rebuild missing notifications after Redis outages or API crashes.
            client.zadd(self.queue_name + ":ready", {job_id: time.time() for job_id in job_ids}, nx=True)
            pending = set(job_ids)
            result = [job_id for job_id in client.zrange(self.queue_name + ":ready", 0, -1) if job_id in pending]
            self.connection_verified = True
            return result
        except Exception as exc:
            self.connection_verified = False
            raise EnterpriseConfigurationError("Redis queue is unavailable; no jobs were claimed") from exc

    def acknowledge(self, job_id: str) -> None:
        try:
            self._client().zrem(self.queue_name + ":ready", job_id)
        except Exception as exc:
            self.connection_verified = False
            raise EnterpriseConfigurationError("Redis acknowledgement failed; durable lease is retained") from exc

    def status_view(self) -> dict[str, Any]:
        driver_ready = False
        if self.configured:
            try:
                import redis  # noqa: F401
                driver_ready = True
            except ImportError:
                pass
        return {"backend": "redis", "configured": self.configured, "driver_ready": driver_ready, "queue": self.queue_name, "production_ready": self.configured and driver_ready and self.connection_verified, "transport": "http-worker-with-redis-index", "connectivity_verified": self.connection_verified}

    def probe(self) -> None:
        self.connection_verified = False
        self.connection_verified = bool(self._client().ping())
        if not self.connection_verified:
            raise EnterpriseConfigurationError("Redis probe failed")

    def _client(self) -> Any:
        if not self.configured:
            raise EnterpriseConfigurationError("MEDIAFORGE_REDIS_URL is required for Redis queue")
        try:
            import redis
        except ImportError as exc:
            raise EnterpriseConfigurationError("redis package is required for Redis queue") from exc
        return redis.Redis.from_url(self.url, decode_responses=True, socket_timeout=5, socket_connect_timeout=5)


class PostgresStateAdapter:
    """Durable PostgreSQL state snapshots for a single active control plane."""

    def __init__(self, url: str, table: str = "mediaforge_state") -> None:
        self.url = url.strip()
        self.table = table.strip() or "mediaforge_state"
        self.version = 0
        self.connection_verified = False

    def _connect(self) -> Any:
        if not self.url:
            raise EnterpriseConfigurationError("MEDIAFORGE_DATABASE_URL is required for PostgreSQL state")
        try:
            import psycopg
        except ImportError as exc:
            raise EnterpriseConfigurationError("install the enterprise extra for PostgreSQL state") from exc
        return psycopg.connect(self.url, connect_timeout=10)

    def load(self) -> str | None:
        from psycopg import sql
        with self._connect() as connection:
            connection.execute(sql.SQL("CREATE TABLE IF NOT EXISTS {} (state_key TEXT PRIMARY KEY, payload TEXT NOT NULL, version BIGINT NOT NULL, updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW())").format(sql.Identifier(self.table)))
            row = connection.execute(sql.SQL("SELECT payload, version FROM {} WHERE state_key = %s").format(sql.Identifier(self.table)), ("workspace",)).fetchone()
        self.version = int(row[1]) if row else 0
        self.connection_verified = True
        return row[0] if row else None

    def save(self, payload: str) -> None:
        from psycopg import sql
        with self._connect() as connection:
            connection.execute(sql.SQL("CREATE TABLE IF NOT EXISTS {} (state_key TEXT PRIMARY KEY, payload TEXT NOT NULL, version BIGINT NOT NULL, updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW())").format(sql.Identifier(self.table)))
            row = connection.execute(sql.SQL("INSERT INTO {} AS state (state_key, payload, version) VALUES (%s, %s, %s) ON CONFLICT(state_key) DO UPDATE SET payload=EXCLUDED.payload, version=EXCLUDED.version, updated_at=NOW() WHERE state.version = %s RETURNING version").format(sql.Identifier(self.table)), ("workspace", payload, self.version + 1, self.version)).fetchone()
            if row is None:
                raise EnterpriseConfigurationError("PostgreSQL state changed in another process")
        self.version = int(row[0])
        self.connection_verified = True

    def probe(self) -> None:
        self.connection_verified = False
        with self._connect() as connection:
            connection.execute("SELECT 1").fetchone()
        self.connection_verified = True

    def status_view(self) -> dict[str, Any]:
        try:
            import psycopg  # noqa: F401
            driver_ready = True
        except ImportError:
            driver_ready = False
        return {"backend": "postgres", "configured": bool(self.url), "driver_ready": driver_ready, "table": self.table, "version": self.version, "topology": "single-control-plane", "connectivity_verified": self.connection_verified}


@dataclass(frozen=True)
class ControlPlaneLeaseSettings:
    mode: str
    holder_id: str
    lease_seconds: int
    table: str

    @classmethod
    def from_env(cls) -> "ControlPlaneLeaseSettings":
        mode = os.getenv("MEDIAFORGE_CONTROL_PLANE_MODE", "single").strip().lower()
        if mode not in {"single", "leased"}:
            raise EnterpriseConfigurationError(
                "MEDIAFORGE_CONTROL_PLANE_MODE must be single or leased"
            )
        try:
            lease_seconds = int(os.getenv("MEDIAFORGE_CONTROL_PLANE_LEASE_SECONDS", "30"))
        except ValueError as exc:
            raise EnterpriseConfigurationError(
                "MEDIAFORGE_CONTROL_PLANE_LEASE_SECONDS must be an integer"
            ) from exc
        if not 5 <= lease_seconds <= 300:
            raise EnterpriseConfigurationError(
                "MEDIAFORGE_CONTROL_PLANE_LEASE_SECONDS must be between 5 and 300"
            )
        table = os.getenv("MEDIAFORGE_CONTROL_PLANE_TABLE", "mediaforge_control_plane_lease").strip()
        if not table.replace("_", "").isalnum():
            raise EnterpriseConfigurationError(
                "MEDIAFORGE_CONTROL_PLANE_TABLE must contain letters, digits and underscores"
            )
        holder_id = os.getenv("MEDIAFORGE_CONTROL_PLANE_ID", "").strip() or f"control-plane-{uuid4().hex}"
        if len(holder_id) > 240:
            raise EnterpriseConfigurationError("MEDIAFORGE_CONTROL_PLANE_ID is too long")
        return cls(mode=mode, holder_id=holder_id, lease_seconds=lease_seconds, table=table)


class PostgresControlPlaneLease:
    """Lease-based single writer for active/passive API control planes.

    The platform state remains a snapshot, therefore multiple active writers would
    lose updates even with an optimistic version column. This lease makes one
    instance the writer at a time; a standby can take over after expiry.
    """

    lease_key = "workspace"

    def __init__(self, state: PostgresStateAdapter, settings: ControlPlaneLeaseSettings) -> None:
        self.state = state
        self.settings = settings
        self.primary = settings.mode == "single"
        self.expires_at: str | None = None
        self.last_error: str | None = None
        self._lock = threading.RLock()

    @property
    def enabled(self) -> bool:
        return self.settings.mode == "leased"

    def acquire_or_renew(self) -> bool:
        if not self.enabled:
            self.primary = True
            self.last_error = None
            return True
        if not self.state.url:
            self.primary = False
            self.last_error = "MEDIAFORGE_DATABASE_URL is required for leased control plane"
            return False
        try:
            from psycopg import sql
            with self._lock, self.state._connect() as connection:
                table = sql.Identifier(self.settings.table)
                connection.execute(
                    sql.SQL(
                        "CREATE TABLE IF NOT EXISTS {} ("
                        "lease_key TEXT PRIMARY KEY, holder_id TEXT NOT NULL, "
                        "expires_at TIMESTAMPTZ NOT NULL, updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW())"
                    ).format(table)
                )
                row = connection.execute(
                    sql.SQL(
                        "INSERT INTO {} AS lease (lease_key, holder_id, expires_at) "
                        "VALUES (%s, %s, NOW() + (%s * INTERVAL '1 second')) "
                        "ON CONFLICT (lease_key) DO UPDATE SET "
                        "holder_id = EXCLUDED.holder_id, expires_at = EXCLUDED.expires_at, updated_at = NOW() "
                        "WHERE lease.expires_at <= NOW() OR lease.holder_id = EXCLUDED.holder_id "
                        "RETURNING holder_id, expires_at"
                    ).format(table),
                    (self.lease_key, self.settings.holder_id, self.settings.lease_seconds),
                ).fetchone()
                if row is None:
                    active = connection.execute(
                        sql.SQL("SELECT holder_id, expires_at FROM {} WHERE lease_key = %s").format(table),
                        (self.lease_key,),
                    ).fetchone()
                    self.primary = False
                    self.expires_at = active[1].isoformat() if active else None
                    self.last_error = "another control plane currently holds the lease"
                    return False
                self.primary = True
                self.expires_at = row[1].isoformat()
                self.last_error = None
                self.state.connection_verified = True
                return True
        except Exception:
            self.primary = False
            self.expires_at = None
            self.last_error = "control-plane lease database operation failed"
            return False

    def release(self) -> None:
        if not self.enabled or not self.state.url:
            return
        try:
            from psycopg import sql
            with self._lock, self.state._connect() as connection:
                connection.execute(
                    sql.SQL("DELETE FROM {} WHERE lease_key = %s AND holder_id = %s").format(
                        sql.Identifier(self.settings.table)
                    ),
                    (self.lease_key, self.settings.holder_id),
                )
        except Exception:
            # The lease has a short expiry, so shutdown never blocks on a transient DB failure.
            pass
        finally:
            self.primary = False

    def status_view(self) -> dict[str, Any]:
        return {
            "mode": self.settings.mode,
            "enabled": self.enabled,
            "holder_id": self.settings.holder_id,
            "lease_seconds": self.settings.lease_seconds if self.enabled else None,
            "table": self.settings.table if self.enabled else None,
            "primary": self.primary,
            "ready_for_traffic": self.primary,
            "expires_at": self.expires_at,
            "last_error": self.last_error,
            "topology": "leased-active-passive" if self.enabled else "single-control-plane",
        }


class EnterpriseRuntime:
    def __init__(self, output_root: Path) -> None:
        self.storage = ObjectStorage.from_env(output_root)
        self.rate_limiter = SlidingWindowRateLimiter.from_env(output_root)
        self.read_rate_limiter = SlidingWindowRateLimiter.from_env(
            output_root,
            limit_env="MEDIAFORGE_RATE_LIMIT_READ_REQUESTS",
            default_limit=self.rate_limiter.limit * 5,
        )
        self.billing = BillingLedger.from_env(output_root)
        self.redis_queue = RedisQueueAdapter(os.getenv("MEDIAFORGE_REDIS_URL", ""), os.getenv("MEDIAFORGE_REDIS_QUEUE", "mediaforge:jobs"))
        self.postgres = PostgresStateAdapter(os.getenv("MEDIAFORGE_DATABASE_URL", ""), os.getenv("MEDIAFORGE_STATE_TABLE", "mediaforge_state"))
        self.control_plane = PostgresControlPlaneLease(
            self.postgres,
            ControlPlaneLeaseSettings.from_env(),
        )

    def probe(self) -> dict[str, Any]:
        adapters = {"storage": self.storage}
        if os.getenv("MEDIAFORGE_QUEUE_BACKEND", "sqlite").strip().lower() == "redis":
            adapters["queue"] = self.redis_queue
        if os.getenv("MEDIAFORGE_STATE_BACKEND", "json").strip().lower() == "postgres":
            adapters["database"] = self.postgres
        checks = []
        for name, adapter in adapters.items():
            try:
                adapter.probe()
                checks.append({"name": name, "passed": True})
            except Exception:
                checks.append({"name": name, "passed": False, "error": "connection probe failed; check endpoint, credentials and driver"})
        return {"checks": checks, "reachable": all(check["passed"] for check in checks), "runtime": self.status_view()}

    def status_view(self) -> dict[str, Any]:
        identity = AuthManager.from_env().status_view()
        identity["production_ready"] = identity["mode"] in {"required", "oidc"} and identity["configured"]
        queue_backend = os.getenv("MEDIAFORGE_QUEUE_BACKEND", "sqlite").strip().lower() or "sqlite"
        state_backend = os.getenv("MEDIAFORGE_STATE_BACKEND", "json").strip().lower()
        queue = self.redis_queue.status_view() if queue_backend == "redis" else {"backend": queue_backend, "configured": queue_backend == "sqlite", "shared_state": state_backend in {"sqlite", "postgres"}, "production_ready": queue_backend == "sqlite" and state_backend in {"sqlite", "postgres"}}
        database = self.postgres.status_view() if state_backend == "postgres" else {"backend": state_backend, "configured": state_backend in {"json", "sqlite"}}
        control_plane = self.control_plane.status_view()
        topology = (
            "leased-active-passive-control-plane-with-remote-workers"
            if control_plane["enabled"]
            else "single-control-plane-with-remote-workers"
        )
        return {"schema_version": "mediaforge-enterprise-runtime-v1", "identity": identity, "storage": self.storage.status_view(), "database": database, "queue": queue, "topology": topology, "control_plane": control_plane, "rate_limit": self.rate_limiter.status_view(), "read_rate_limit": self.read_rate_limiter.status_view(), "billing": self.billing.status_view()}
