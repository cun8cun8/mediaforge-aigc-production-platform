from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from .oidc import OIDCIntrospectionError, OIDCIntrospector
from .sessions import (
    BrowserSessionStore,
    BrowserSessionStoreError,
    InMemoryBrowserSessionStore,
    build_browser_session_store_from_env,
)


class AuthConfigurationError(ValueError):
    pass


class AuthenticationError(ValueError):
    def __init__(self, message: str, *, status_code: int = 401) -> None:
        super().__init__(message)
        self.status_code = status_code


@dataclass(frozen=True)
class Principal:
    subject: str
    role: str
    tenant_id: str | None = None
    authenticated: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "subject": self.subject,
            "role": self.role,
            "tenant_id": self.tenant_id,
            "authenticated": self.authenticated,
        }


class AuthManager:
    role_levels = {
        "viewer": 10,
        "provider": 20,
        "orchestrator": 20,
        "reviewer": 30,
        "editor": 30,
        "publisher": 40,
        "admin": 50,
    }

    def __init__(
        self,
        *,
        mode: str,
        credentials: dict[str, Principal],
        oidc: OIDCIntrospector | None = None,
        browser_authorization_url: str = "",
        browser_token_url: str = "",
        browser_redirect_uri: str = "",
        browser_scope: str = "openid profile",
        session_ttl_seconds: int = 43200,
        browser_session_store: BrowserSessionStore | None = None,
        oidc_role_claim: str = "",
        oidc_tenant_claim: str = "",
        oidc_role_mapping: dict[str, str] | None = None,
    ) -> None:
        self.mode = mode
        self._credentials = credentials
        self._oidc = oidc
        self._browser_authorization_url = browser_authorization_url.strip()
        self._browser_token_url = browser_token_url.strip()
        self._browser_redirect_uri = browser_redirect_uri.strip()
        self._browser_scope = browser_scope.strip() or "openid profile"
        self._session_ttl_seconds = session_ttl_seconds
        self._browser_session_store = browser_session_store or build_browser_session_store_from_env()
        self._oidc_role_claim = self._validate_claim_path(oidc_role_claim, "MEDIAFORGE_OIDC_ROLE_CLAIM")
        self._oidc_tenant_claim = self._validate_claim_path(oidc_tenant_claim, "MEDIAFORGE_OIDC_TENANT_CLAIM")
        self._oidc_role_mapping = dict(oidc_role_mapping or {})

    @classmethod
    def from_env(cls) -> "AuthManager":
        mode = os.getenv("MEDIAFORGE_AUTH_MODE", "disabled").strip().lower()
        if mode not in {"disabled", "optional", "required", "oidc"}:
            raise AuthConfigurationError(
                "MEDIAFORGE_AUTH_MODE must be disabled, optional, required, or oidc"
            )
        raw = os.getenv("MEDIAFORGE_API_KEYS", "").strip()
        credentials: dict[str, Principal] = {}
        if raw:
            try:
                payload = json.loads(raw)
            except json.JSONDecodeError as exc:
                raise AuthConfigurationError(
                    "MEDIAFORGE_API_KEYS must be a JSON object"
                ) from exc
            if not isinstance(payload, dict):
                raise AuthConfigurationError("MEDIAFORGE_API_KEYS must be a JSON object")
            for token, raw_principal in payload.items():
                if not isinstance(token, str) or not token.strip():
                    raise AuthConfigurationError("MEDIAFORGE_API_KEYS contains an empty token")
                if isinstance(raw_principal, str):
                    principal = Principal(
                        subject=raw_principal,
                        role="viewer",
                        authenticated=True,
                    )
                elif isinstance(raw_principal, dict):
                    role = str(raw_principal.get("role") or "viewer").strip().lower()
                    if role not in cls.role_levels:
                        raise AuthConfigurationError(f"unsupported API key role: {role}")
                    subject = str(raw_principal.get("subject") or token[:8]).strip()
                    tenant_id = raw_principal.get("tenant_id")
                    normalized_tenant_id = str(tenant_id).strip() if tenant_id else None
                    if role == "orchestrator" and not normalized_tenant_id:
                        raise AuthConfigurationError(
                            "orchestrator API keys must declare a tenant_id"
                        )
                    principal = Principal(
                        subject=subject,
                        role=role,
                        tenant_id=normalized_tenant_id,
                        authenticated=True,
                    )
                else:
                    raise AuthConfigurationError(
                        "MEDIAFORGE_API_KEYS values must be role strings or objects"
                    )
                credentials[token] = principal
        oidc_url = os.getenv("MEDIAFORGE_OIDC_INTROSPECTION_URL", "").strip()
        if mode == "oidc" and not oidc_url:
            raise AuthConfigurationError("MEDIAFORGE_OIDC_INTROSPECTION_URL is required when auth mode is oidc")
        try:
            oidc_timeout = float(os.getenv("MEDIAFORGE_OIDC_TIMEOUT_SECONDS", "5"))
        except ValueError as exc:
            raise AuthConfigurationError("MEDIAFORGE_OIDC_TIMEOUT_SECONDS must be a number") from exc
        if oidc_timeout <= 0:
            raise AuthConfigurationError("MEDIAFORGE_OIDC_TIMEOUT_SECONDS must be > 0")
        oidc = OIDCIntrospector(oidc_url, client_id=os.getenv("MEDIAFORGE_OIDC_CLIENT_ID", ""), client_secret=os.getenv("MEDIAFORGE_OIDC_CLIENT_SECRET", ""), timeout=oidc_timeout) if oidc_url else None
        if mode == "required" and not credentials:
            raise AuthConfigurationError(
                "MEDIAFORGE_API_KEYS must contain at least one key when auth is required"
            )
        try:
            session_ttl_seconds = int(os.getenv("MEDIAFORGE_OIDC_SESSION_TTL_SECONDS", "43200"))
        except ValueError as exc:
            raise AuthConfigurationError("MEDIAFORGE_OIDC_SESSION_TTL_SECONDS must be an integer") from exc
        if not 60 <= session_ttl_seconds <= 604800:
            raise AuthConfigurationError("MEDIAFORGE_OIDC_SESSION_TTL_SECONDS must be between 60 and 604800")
        try:
            browser_session_store = (
                build_browser_session_store_from_env()
                if mode == "oidc"
                else InMemoryBrowserSessionStore()
            )
        except BrowserSessionStoreError as exc:
            raise AuthConfigurationError(str(exc)) from exc
        raw_role_mapping = os.getenv("MEDIAFORGE_OIDC_ROLE_MAPPING", "").strip()
        role_mapping: dict[str, str] = {}
        if raw_role_mapping:
            try:
                decoded_mapping = json.loads(raw_role_mapping)
            except json.JSONDecodeError as exc:
                raise AuthConfigurationError("MEDIAFORGE_OIDC_ROLE_MAPPING must be a JSON object") from exc
            if not isinstance(decoded_mapping, dict):
                raise AuthConfigurationError("MEDIAFORGE_OIDC_ROLE_MAPPING must be a JSON object")
            for external_role, mediaforge_role in decoded_mapping.items():
                source = str(external_role).strip().lower()
                target = str(mediaforge_role).strip().lower()
                if not source or len(source) > 240 or target not in cls.role_levels:
                    raise AuthConfigurationError("OIDC role mapping contains an invalid role")
                role_mapping[source] = target
        return cls(
            mode=mode,
            credentials=credentials,
            oidc=oidc,
            browser_authorization_url=os.getenv("MEDIAFORGE_OIDC_AUTHORIZATION_URL", ""),
            browser_token_url=os.getenv("MEDIAFORGE_OIDC_TOKEN_URL", ""),
            browser_redirect_uri=os.getenv("MEDIAFORGE_OIDC_REDIRECT_URI", ""),
            browser_scope=os.getenv("MEDIAFORGE_OIDC_SCOPE", "openid profile"),
            session_ttl_seconds=session_ttl_seconds,
            browser_session_store=browser_session_store,
            oidc_role_claim=os.getenv("MEDIAFORGE_OIDC_ROLE_CLAIM", ""),
            oidc_tenant_claim=os.getenv("MEDIAFORGE_OIDC_TENANT_CLAIM", ""),
            oidc_role_mapping=role_mapping,
        )

    @property
    def configured(self) -> bool:
        return bool(self._credentials) or (self.mode == "oidc" and self._oidc is not None)

    def status_view(self) -> dict[str, Any]:
        return {
            "schema_version": "mediaforge-auth-v1",
            "mode": self.mode,
            "configured": self.configured,
            "credential_count": len(self._credentials),
            "roles": sorted({principal.role for principal in self._credentials.values()}),
            "token_format": "Authorization: Bearer <token>",
            "oidc": self._oidc.status_view() if self._oidc else {"configured": False},
            "directory_mapping": {
                "role_claim": self._oidc_role_claim or "roles|realm_access.roles",
                "tenant_claim": self._oidc_tenant_claim or "tenant_id|tenant|org_id",
                "role_mapping_count": len(self._oidc_role_mapping),
            },
            "browser_login": {
                "configured": self.browser_login_configured,
                "authorization_endpoint": self._display_endpoint(self._browser_authorization_url),
                "token_endpoint_configured": bool(self._browser_token_url),
                "redirect_uri": self._browser_redirect_uri or None,
                "scope": self._browser_scope if self.browser_login_configured else None,
                "session_ttl_seconds": self._session_ttl_seconds if self.browser_login_configured else None,
                "pkce": "S256" if self.browser_login_configured else None,
                "session_store": self._browser_session_store.status_view(),
            },
        }

    @property
    def browser_login_configured(self) -> bool:
        return bool(
            self.mode == "oidc"
            and self._oidc is not None
            and self._oidc.client_id
            and self._browser_authorization_url
            and self._browser_token_url
            and self._browser_redirect_uri
        )

    @staticmethod
    def _display_endpoint(value: str) -> str | None:
        parsed = urlparse(value)
        return f"{parsed.scheme}://{parsed.netloc}{parsed.path}" if parsed.scheme and parsed.netloc else None

    def _prune_browser_state(self) -> None:
        try:
            self._browser_session_store.prune()
        except BrowserSessionStoreError as exc:
            raise AuthenticationError(
                "OIDC browser session store is unavailable", status_code=503
            ) from exc

    def begin_browser_login(self) -> str:
        if not self.browser_login_configured:
            raise AuthenticationError("browser OIDC login is not configured", status_code=503)
        self._prune_browser_state()
        state = secrets.token_urlsafe(32)
        verifier = secrets.token_urlsafe(64)
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest()).rstrip(b"=").decode("ascii")
        try:
            self._browser_session_store.create_state(state, verifier, time.time() + 600)
        except BrowserSessionStoreError as exc:
            raise AuthenticationError(
                "OIDC browser session store is unavailable", status_code=503
            ) from exc
        parsed = urlparse(self._browser_authorization_url)
        query = dict(parse_qsl(parsed.query, keep_blank_values=True))
        query.update({
            "response_type": "code",
            "client_id": self._oidc.client_id if self._oidc else "",
            "redirect_uri": self._browser_redirect_uri,
            "scope": self._browser_scope,
            "state": state,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        })
        return urlunparse(parsed._replace(query=urlencode(query)))

    def complete_browser_login(self, *, code: str, state: str) -> tuple[str, Principal, int]:
        if not self.browser_login_configured or self._oidc is None:
            raise AuthenticationError("browser OIDC login is not configured", status_code=503)
        self._prune_browser_state()
        try:
            verifier = self._browser_session_store.consume_state(state)
        except BrowserSessionStoreError as exc:
            raise AuthenticationError(
                "OIDC browser session store is unavailable", status_code=503
            ) from exc
        if not verifier or not code.strip() or len(code) > 4096:
            raise AuthenticationError("OIDC login state is invalid or expired")
        try:
            token_payload = self._oidc.exchange_authorization_code(
                self._browser_token_url,
                code=code.strip(),
                redirect_uri=self._browser_redirect_uri,
                code_verifier=verifier,
            )
            principal = self._principal_from_oidc_token(str(token_payload["access_token"]))
        except OIDCIntrospectionError as exc:
            raise AuthenticationError(str(exc)) from exc
        expires_in = token_payload.get("expires_in", self._session_ttl_seconds)
        try:
            ttl = int(expires_in)
        except (TypeError, ValueError):
            ttl = self._session_ttl_seconds
        ttl = min(max(ttl, 60), self._session_ttl_seconds)
        session_id = secrets.token_urlsafe(48)
        try:
            self._browser_session_store.create_session(
                session_id, principal.as_dict(), time.time() + ttl
            )
        except BrowserSessionStoreError as exc:
            raise AuthenticationError(
                "OIDC browser session store is unavailable", status_code=503
            ) from exc
        return session_id, principal, ttl

    def logout_browser_session(self, session_id: str | None) -> None:
        if session_id:
            try:
                self._browser_session_store.delete_session(session_id)
            except BrowserSessionStoreError as exc:
                raise AuthenticationError(
                    "OIDC browser session store is unavailable", status_code=503
                ) from exc

    def authenticate(self, authorization: str | None, *, session_id: str | None = None) -> Principal:
        self._prune_browser_state()
        if not authorization and session_id:
            try:
                session = self._browser_session_store.get_session(session_id)
            except BrowserSessionStoreError as exc:
                raise AuthenticationError(
                    "OIDC browser session store is unavailable", status_code=503
                ) from exc
            if session:
                try:
                    subject = str(session["subject"]).strip()
                    role = str(session["role"]).strip().lower()
                    tenant_id = str(session.get("tenant_id") or "").strip() or None
                except (KeyError, TypeError, ValueError) as exc:
                    raise AuthenticationError("OIDC browser session is invalid") from exc
                if not subject or role not in self.role_levels or not tenant_id:
                    raise AuthenticationError("OIDC browser session is invalid")
                return Principal(
                    subject=subject,
                    role=role,
                    tenant_id=tenant_id,
                    authenticated=True,
                )
        if not authorization:
            if self.mode in {"required", "oidc"}:
                raise AuthenticationError("Bearer authentication is required")
            return Principal(subject="local-development", role="admin")
        scheme, _, token = authorization.partition(" ")
        if scheme.lower() != "bearer" or not token.strip():
            raise AuthenticationError("Authorization must use a Bearer token")
        for configured_token, principal in self._credentials.items():
            if hmac.compare_digest(token.strip(), configured_token):
                return principal
        if self.mode == "oidc" and self._oidc is not None:
            return self._principal_from_oidc_token(token.strip())
        raise AuthenticationError("Bearer token is invalid")

    def _principal_from_oidc_token(self, token: str) -> Principal:
        if self._oidc is None:
            raise AuthenticationError("OIDC is not configured")
        try:
            payload = self._oidc.introspect(token)
        except OIDCIntrospectionError as exc:
            raise AuthenticationError(str(exc)) from exc
        subject = str(payload.get("sub") or payload.get("username") or "").strip()
        if not subject:
            raise AuthenticationError("OIDC token has no subject")
        realm = payload.get("realm_access") or {}
        if not isinstance(realm, dict):
            raise AuthenticationError("OIDC realm_access must be an object")
        raw_roles = (
            self._claim_value(payload, self._oidc_role_claim)
            if self._oidc_role_claim
            else payload.get("roles") or realm.get("roles", [])
        )
        roles = [raw_roles] if isinstance(raw_roles, str) else raw_roles
        if not isinstance(roles, list):
            raise AuthenticationError("OIDC roles must be a list or string")
        role = next(
            (
                self._oidc_role_mapping.get(str(item).strip().lower(), str(item).strip().lower())
                for item in roles
                if self._oidc_role_mapping.get(str(item).strip().lower(), str(item).strip().lower()) in self.role_levels
            ),
            "viewer",
        )
        tenant_id = (
            self._claim_value(payload, self._oidc_tenant_claim)
            if self._oidc_tenant_claim
            else payload.get("tenant_id") or payload.get("tenant") or payload.get("org_id")
        )
        if not isinstance(tenant_id, str) or not tenant_id.strip():
            raise AuthenticationError("OIDC token must include a tenant identifier")
        return Principal(subject=subject, role=role, tenant_id=tenant_id.strip(), authenticated=True)

    @staticmethod
    def _validate_claim_path(value: str, variable: str) -> str:
        path = value.strip()
        if not path:
            return ""
        if len(path) > 200 or any(
            not segment.replace("_", "").isalnum() for segment in path.split(".")
        ):
            raise AuthConfigurationError(f"{variable} must be a dot-separated claim path")
        return path

    @staticmethod
    def _claim_value(payload: dict[str, object], path: str) -> object | None:
        value: object = payload
        for segment in path.split("."):
            if not isinstance(value, dict):
                return None
            value = value.get(segment)
        return value

    def authorize(self, principal: Principal, *, method: str, path: str) -> None:
        if self.mode == "disabled":
            return
        if path == "/auth/logout":
            return
        if principal.role == "orchestrator":
            if self.is_temporal_activity_process(method=method, path=path):
                return
            raise AuthenticationError(
                "orchestrator credentials are restricted to Temporal activity paths",
                status_code=403,
            )
        if (
            path.startswith("/providers/")
            and path.endswith("/circuit/recover")
            and method.upper() == "POST"
            and principal.role != "admin"
        ):
            raise AuthenticationError(
                "provider circuit recovery requires an admin role",
                status_code=403,
            )
        if (
            path.startswith("/ops/alerts/")
            and path.endswith("/acknowledge")
            and method.upper() == "POST"
            and principal.role != "admin"
        ):
            raise AuthenticationError(
                "operations alert acknowledgement requires an admin role",
                status_code=403,
            )
        if (
            path == "/content-credentials/attest"
            and method.upper() == "POST"
            and principal.role != "admin"
        ):
            raise AuthenticationError(
                "C2PA production attestation requires an admin role",
                status_code=403,
            )
        if path == "/providers/warmup" and method.upper() == "POST" and principal.role != "admin":
            raise AuthenticationError("provider warmup requires an admin role", status_code=403)
        if path == "/planning/probe" and method.upper() == "POST" and principal.role != "admin":
            raise AuthenticationError("planning checkpoint probe requires an admin role", status_code=403)
        if path in {"/billing/events", "/billing/settlements"} and method.upper() == "POST" and principal.role not in {"admin", "provider"}:
            raise AuthenticationError("billing ingestion requires an admin or provider role", status_code=403)
        required_role = "viewer"
        if (
            path.startswith("/providers/")
            and path.endswith("/circuit/recover")
            and method.upper() == "POST"
        ):
            required_role = "admin"
        elif (
            path.startswith("/ops/alerts/")
            and path.endswith("/acknowledge")
            and method.upper() == "POST"
        ):
            required_role = "admin"
        elif path == "/content-credentials/attest" and method.upper() == "POST":
            required_role = "admin"
        elif path == "/providers/warmup" and method.upper() == "POST":
            required_role = "admin"
        elif path == "/planning/probe" and method.upper() == "POST":
            required_role = "admin"
        elif path in {"/billing/events", "/billing/settlements"} and method.upper() == "POST":
            required_role = "provider"
        elif path.startswith("/enterprise/"):
            required_role = "viewer" if method.upper() == "GET" else "admin"
        elif path.startswith("/governance/"):
            required_role = "admin" if method.upper() != "GET" else "viewer"
        elif path.startswith("/workers"):
            required_role = "viewer" if method.upper() == "GET" else "provider"
        elif self.is_worker_process(method=method, path=path) and principal.role == "provider":
            required_role = "provider"
        elif path.endswith("/callback"):
            required_role = "provider"
        elif "/delivery-feedback" in path:
            required_role = (
                "viewer"
                if method.upper() == "GET"
                else "reviewer"
                if method.upper() == "POST"
                else "editor"
            )
        elif "/review" in path or path.endswith("/approve-ready"):
            required_role = "reviewer"
        elif any(
            marker in path
            for marker in ("/release", "/deliveries", "/closeout", "/archive-package")
        ):
            required_role = "publisher"
        elif method.upper() in {"POST", "PUT", "PATCH", "DELETE"}:
            required_role = "editor"
        if self.role_levels.get(principal.role, 0) < self.role_levels[required_role]:
            raise AuthenticationError(
                f"role {principal.role!r} is not allowed for this operation",
                status_code=403,
            )

    @staticmethod
    def is_worker_process(*, method: str, path: str) -> bool:
        parts = path.strip("/").split("/")
        return method.upper() == "POST" and len(parts) == 5 and parts[0] == "projects" and parts[2] == "jobs" and parts[4] == "process"

    @staticmethod
    def is_temporal_activity_process(*, method: str, path: str) -> bool:
        """Limit the durable Worker to existing, policy-enforcing operations."""
        if method.upper() != "POST":
            return False
        parts = path.strip("/").split("/")
        if len(parts) == 3 and parts[0] == "projects" and parts[2] in {"export", "package"}:
            return True
        if len(parts) == 5 and parts[0] == "projects" and parts[2] == "shots" and parts[4] == "submit":
            return True
        return len(parts) == 4 and parts[0] == "projects" and parts[2:] == ["deliveries", "dispatch"]
