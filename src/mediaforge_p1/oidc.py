from __future__ import annotations

import base64
import json
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlparse
from urllib.request import Request, urlopen


class OIDCIntrospectionError(ValueError):
    pass


class OIDCIntrospector:
    """RFC 7662-style opaque-token introspection for enterprise SSO gateways."""

    def __init__(self, url: str, *, client_id: str = "", client_secret: str = "", timeout: float = 5.0) -> None:
        self.url = url.strip()
        self.client_id = client_id.strip()
        self.client_secret = client_secret
        self.timeout = timeout

    def introspect(self, token: str) -> dict[str, object]:
        request = Request(self.url, data=urlencode({"token": token}).encode("utf-8"), headers={"Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json"}, method="POST")
        if self.client_id and self.client_secret:
            value = base64.b64encode(f"{self.client_id}:{self.client_secret}".encode("utf-8")).decode("ascii")
            request.add_header("Authorization", f"Basic {value}")
        try:
            with urlopen(request, timeout=self.timeout) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except (HTTPError, URLError, TimeoutError, OSError, ValueError) as exc:
            raise OIDCIntrospectionError("OIDC token introspection failed") from exc
        if not isinstance(payload, dict) or payload.get("active") is not True:
            raise OIDCIntrospectionError("OIDC token is inactive")
        return payload

    def exchange_authorization_code(
        self,
        token_url: str,
        *,
        code: str,
        redirect_uri: str,
        code_verifier: str,
    ) -> dict[str, object]:
        """Exchange a browser authorization code without persisting bearer tokens."""
        endpoint = token_url.strip()
        if not endpoint or not code.strip() or not redirect_uri.strip() or not code_verifier.strip():
            raise OIDCIntrospectionError("OIDC browser login is not configured")
        body = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
            "code_verifier": code_verifier,
        }
        if self.client_id:
            body["client_id"] = self.client_id
        request = Request(
            endpoint,
            data=urlencode(body).encode("utf-8"),
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "Accept": "application/json",
            },
            method="POST",
        )
        if self.client_id and self.client_secret:
            value = base64.b64encode(f"{self.client_id}:{self.client_secret}".encode("utf-8")).decode("ascii")
            request.add_header("Authorization", f"Basic {value}")
        try:
            with urlopen(request, timeout=self.timeout) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except (HTTPError, URLError, TimeoutError, OSError, ValueError) as exc:
            raise OIDCIntrospectionError("OIDC authorization-code exchange failed") from exc
        if not isinstance(payload, dict) or not isinstance(payload.get("access_token"), str) or not payload["access_token"].strip():
            raise OIDCIntrospectionError("OIDC authorization-code exchange returned no access token")
        return payload

    def status_view(self) -> dict[str, object]:
        parsed = urlparse(self.url)
        return {"configured": bool(self.url), "endpoint": f"{parsed.scheme}://{parsed.netloc}{parsed.path}" if parsed.netloc else None, "client_credentials_configured": bool(self.client_id and self.client_secret), "timeout_seconds": self.timeout}
