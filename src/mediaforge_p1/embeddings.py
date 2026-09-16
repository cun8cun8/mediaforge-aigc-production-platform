from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from .memory import MemoryUnavailable


class NoEmbeddingRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


@dataclass
class TEIEmbedder:
    url: str
    model: str
    dimensions: int
    token: str = field(default="", repr=False)
    timeout_seconds: float = 30.0
    query_prefix: str = ""
    document_prefix: str = ""
    allow_data_export: bool = False
    verified: bool = field(default=False, init=False)

    def __post_init__(self):
        parsed = urlsplit(self.url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("embedding URL must be HTTP(S) without credentials, query or fragment")
        if not self.model.strip() or not 1 <= self.dimensions <= 4096:
            raise ValueError("embedding model and dimensions (1..4096) are required")
        if not math.isfinite(self.timeout_seconds) or not 0 < self.timeout_seconds <= 120:
            raise ValueError("embedding timeout must be finite and between 0 and 120 seconds")

    def embed(self, texts: list[str], *, query: bool = False) -> list[list[float]]:
        if not texts:
            return []
        if not self.allow_data_export:
            self.verified = False
            raise MemoryUnavailable("embedding data export is disabled")
        if len(texts) > 32 or any(not isinstance(text, str) or not text.strip() or len(text) > 4000 for text in texts):
            raise MemoryUnavailable("embedding input must be 1..32 nonempty texts of at most 4000 characters")
        prefix = self.query_prefix if query else self.document_prefix
        body = json.dumps({"inputs": [prefix + text for text in texts], "truncate": False}, ensure_ascii=False).encode("utf-8")
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        try:
            # Do not forward project text or credentials to a redirected destination.
            with build_opener(NoEmbeddingRedirects()).open(Request(self.url, data=body, headers=headers, method="POST"), timeout=self.timeout_seconds) as response:
                raw = response.read(8 * 1024 * 1024 + 1)
            if len(raw) > 8 * 1024 * 1024:
                raise ValueError("embedding response too large")
            vectors = json.loads(raw)
            if not isinstance(vectors, list) or len(vectors) != len(texts):
                raise ValueError("embedding count does not match input")
            normalized = []
            for vector in vectors:
                if not isinstance(vector, list) or len(vector) != self.dimensions:
                    raise ValueError("embedding dimension mismatch")
                if any(isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) for value in vector):
                    raise ValueError("embedding values must be finite numbers")
                norm = math.hypot(*vector)
                if not math.isfinite(norm) or norm == 0:
                    raise ValueError("embedding norm must be finite and positive")
                normalized.append([float(value) / norm for value in vector])
            self.verified = True
            return normalized
        except (HTTPError, URLError, OSError, ValueError, TypeError, OverflowError) as exc:
            self.verified = False
            raise MemoryUnavailable("embedding service failed or returned invalid vectors") from exc
