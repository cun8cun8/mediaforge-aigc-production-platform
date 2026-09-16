from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from typing import Any, Protocol


class BrowserSessionStoreError(RuntimeError):
    pass


class BrowserSessionStore(Protocol):
    def prune(self) -> None: ...
    def create_state(self, state: str, verifier: str, expires_at: float) -> None: ...
    def consume_state(self, state: str) -> str | None: ...
    def create_session(self, session_id: str, principal: dict[str, Any], expires_at: float) -> None: ...
    def get_session(self, session_id: str) -> dict[str, Any] | None: ...
    def delete_session(self, session_id: str) -> None: ...
    def status_view(self) -> dict[str, object]: ...


class InMemoryBrowserSessionStore:
    name = "memory"

    def __init__(self) -> None:
        self._states: dict[str, tuple[str, float]] = {}
        self._sessions: dict[str, tuple[dict[str, Any], float]] = {}

    def prune(self) -> None:
        now = time.time()
        self._states = {key: value for key, value in self._states.items() if value[1] > now}
        self._sessions = {key: value for key, value in self._sessions.items() if value[1] > now}

    def create_state(self, state: str, verifier: str, expires_at: float) -> None:
        self.prune()
        self._states[state] = (verifier, expires_at)

    def consume_state(self, state: str) -> str | None:
        self.prune()
        record = self._states.pop(state, None)
        return record[0] if record else None

    def create_session(self, session_id: str, principal: dict[str, Any], expires_at: float) -> None:
        self.prune()
        self._sessions[session_id] = (dict(principal), expires_at)

    def get_session(self, session_id: str) -> dict[str, Any] | None:
        self.prune()
        record = self._sessions.get(session_id)
        return dict(record[0]) if record else None

    def delete_session(self, session_id: str) -> None:
        self._sessions.pop(session_id, None)

    def status_view(self) -> dict[str, object]:
        return {"backend": self.name, "configured": True, "durable": False, "shared": False}


@dataclass(frozen=True)
class PostgresBrowserSessionStore:
    database_url: str
    table: str

    name = "postgres"

    def __post_init__(self) -> None:
        if not self.database_url.strip():
            raise BrowserSessionStoreError(
                "MEDIAFORGE_DATABASE_URL is required for PostgreSQL OIDC sessions"
            )
        if not self.table.replace("_", "").isalnum():
            raise BrowserSessionStoreError(
                "MEDIAFORGE_OIDC_SESSION_TABLE must contain letters, digits and underscores"
            )

    def _connect(self) -> Any:
        try:
            import psycopg
        except ImportError as exc:
            raise BrowserSessionStoreError(
                "install the enterprise extra for PostgreSQL OIDC sessions"
            ) from exc
        try:
            return psycopg.connect(self.database_url, connect_timeout=10)
        except Exception as exc:
            raise BrowserSessionStoreError("PostgreSQL OIDC session store is unavailable") from exc

    def _ensure_schema(self, connection: Any) -> Any:
        from psycopg import sql
        table = sql.Identifier(self.table)
        connection.execute(
            sql.SQL(
                "CREATE TABLE IF NOT EXISTS {} ("
                "record_type TEXT NOT NULL, record_key TEXT NOT NULL, payload TEXT NOT NULL, "
                "expires_at TIMESTAMPTZ NOT NULL, created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(), "
                "PRIMARY KEY(record_type, record_key))"
            ).format(table)
        )
        connection.execute(
            sql.SQL("CREATE INDEX IF NOT EXISTS {} ON {} (expires_at)").format(
                sql.Identifier(f"{self.table}_expires_at"), table
            )
        )
        return table

    def prune(self) -> None:
        try:
            from psycopg import sql
            with self._connect() as connection:
                table = self._ensure_schema(connection)
                connection.execute(sql.SQL("DELETE FROM {} WHERE expires_at <= NOW()").format(table))
        except BrowserSessionStoreError:
            raise
        except Exception as exc:
            raise BrowserSessionStoreError("PostgreSQL OIDC session store is unavailable") from exc

    def create_state(self, state: str, verifier: str, expires_at: float) -> None:
        self._upsert("state", state, {"verifier": verifier}, expires_at)

    def consume_state(self, state: str) -> str | None:
        try:
            from psycopg import sql
            with self._connect() as connection:
                table = self._ensure_schema(connection)
                row = connection.execute(
                    sql.SQL(
                        "DELETE FROM {} WHERE record_type = %s AND record_key = %s "
                        "AND expires_at > NOW() RETURNING payload"
                    ).format(table),
                    ("state", state),
                ).fetchone()
        except BrowserSessionStoreError:
            raise
        except Exception as exc:
            raise BrowserSessionStoreError("PostgreSQL OIDC session store is unavailable") from exc
        if not row:
            return None
        try:
            payload = json.loads(row[0])
        except (TypeError, json.JSONDecodeError):
            return None
        verifier = payload.get("verifier") if isinstance(payload, dict) else None
        return verifier if isinstance(verifier, str) else None

    def create_session(self, session_id: str, principal: dict[str, Any], expires_at: float) -> None:
        self._upsert("session", session_id, principal, expires_at)

    def get_session(self, session_id: str) -> dict[str, Any] | None:
        try:
            from psycopg import sql
            with self._connect() as connection:
                table = self._ensure_schema(connection)
                row = connection.execute(
                    sql.SQL(
                        "SELECT payload FROM {} WHERE record_type = %s AND record_key = %s "
                        "AND expires_at > NOW()"
                    ).format(table),
                    ("session", session_id),
                ).fetchone()
        except BrowserSessionStoreError:
            raise
        except Exception as exc:
            raise BrowserSessionStoreError("PostgreSQL OIDC session store is unavailable") from exc
        if not row:
            return None
        try:
            payload = json.loads(row[0])
        except (TypeError, json.JSONDecodeError):
            return None
        return payload if isinstance(payload, dict) else None

    def delete_session(self, session_id: str) -> None:
        try:
            from psycopg import sql
            with self._connect() as connection:
                table = self._ensure_schema(connection)
                connection.execute(
                    sql.SQL("DELETE FROM {} WHERE record_type = %s AND record_key = %s").format(table),
                    ("session", session_id),
                )
        except BrowserSessionStoreError:
            raise
        except Exception as exc:
            raise BrowserSessionStoreError("PostgreSQL OIDC session store is unavailable") from exc

    def _upsert(self, record_type: str, key: str, payload: dict[str, Any], expires_at: float) -> None:
        if expires_at <= time.time():
            raise BrowserSessionStoreError("OIDC session expiry must be in the future")
        try:
            from psycopg import sql
            with self._connect() as connection:
                table = self._ensure_schema(connection)
                connection.execute(
                    sql.SQL(
                        "INSERT INTO {} AS session (record_type, record_key, payload, expires_at) "
                        "VALUES (%s, %s, %s, TO_TIMESTAMP(%s)) "
                        "ON CONFLICT(record_type, record_key) DO UPDATE SET "
                        "payload = EXCLUDED.payload, expires_at = EXCLUDED.expires_at"
                    ).format(table),
                    (record_type, key, json.dumps(payload, ensure_ascii=True), expires_at),
                )
        except BrowserSessionStoreError:
            raise
        except Exception as exc:
            raise BrowserSessionStoreError("PostgreSQL OIDC session store is unavailable") from exc

    def status_view(self) -> dict[str, object]:
        try:
            import psycopg  # noqa: F401
            driver_ready = True
        except ImportError:
            driver_ready = False
        return {
            "backend": self.name,
            "configured": bool(self.database_url),
            "driver_ready": driver_ready,
            "durable": True,
            "shared": True,
            "table": self.table,
        }


def build_browser_session_store_from_env() -> BrowserSessionStore:
    configured = os.getenv("MEDIAFORGE_OIDC_SESSION_BACKEND", "auto").strip().lower()
    if configured == "auto":
        configured = (
            "postgres"
            if os.getenv("MEDIAFORGE_CONTROL_PLANE_MODE", "single").strip().lower() == "leased"
            else "memory"
        )
    if configured == "memory":
        return InMemoryBrowserSessionStore()
    if configured == "postgres":
        return PostgresBrowserSessionStore(
            database_url=os.getenv("MEDIAFORGE_DATABASE_URL", ""),
            table=os.getenv("MEDIAFORGE_OIDC_SESSION_TABLE", "mediaforge_oidc_session"),
        )
    raise BrowserSessionStoreError(
        "MEDIAFORGE_OIDC_SESSION_BACKEND must be auto, memory or postgres"
    )
