from __future__ import annotations

import hashlib
import json
import os
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .visual import sample_frames


class QualityEvaluationError(RuntimeError):
    pass


@dataclass(frozen=True)
class QualityEvaluation:
    configured: bool
    passed: bool | None
    score: float | None
    provider: str
    checks: list[dict[str, object]]
    error: str | None = None

    def as_dict(self) -> dict[str, object]:
        return {
            "configured": self.configured,
            "passed": self.passed,
            "score": self.score,
            "provider": self.provider,
            "checks": self.checks,
            "error": self.error,
        }


class QualityEvaluator:
    """Optional HTTP quality/moderation evaluator with explicit fail-open policy."""

    def __init__(
        self,
        *,
        url: str = "",
        timeout_seconds: float = 15.0,
        retries: int = 1,
        fail_open: bool = True,
        token: str = "",
        allow_data_export: bool = False,
    ) -> None:
        self.url = url.strip()
        self.timeout_seconds = max(float(timeout_seconds), 1.0)
        self.retries = max(int(retries), 0)
        self.fail_open = bool(fail_open)
        self.token = token.strip()
        self.allow_data_export = bool(allow_data_export)

    @classmethod
    def from_env(cls) -> "QualityEvaluator":
        try:
            timeout = float(os.getenv("MEDIAFORGE_QUALITY_TIMEOUT_SECONDS", "15"))
        except ValueError as exc:
            raise ValueError("MEDIAFORGE_QUALITY_TIMEOUT_SECONDS must be a number") from exc
        try:
            retries = int(os.getenv("MEDIAFORGE_QUALITY_RETRIES", "1"))
        except ValueError as exc:
            raise ValueError("MEDIAFORGE_QUALITY_RETRIES must be an integer") from exc
        return cls(
            url=os.getenv("MEDIAFORGE_QUALITY_URL", ""),
            timeout_seconds=timeout,
            retries=retries,
            fail_open=os.getenv("MEDIAFORGE_QUALITY_FAIL_OPEN", "true").lower()
            not in {"0", "false", "no", "off"},
            token=os.getenv("MEDIAFORGE_QUALITY_TOKEN", ""),
            allow_data_export=os.getenv(
                "MEDIAFORGE_QUALITY_ALLOW_DATA_EXPORT",
                "false",
            ).lower()
            in {"1", "true", "yes", "on"},
        )

    def status_view(self) -> dict[str, object]:
        return {
            "configured": bool(self.url),
            "enabled": bool(self.url and self.allow_data_export),
            "url": self.url or None,
            "timeout_seconds": self.timeout_seconds,
            "retries": self.retries,
            "fail_open": self.fail_open,
            "token_configured": bool(self.token),
        }

    def evaluate(
        self,
        artifact_path: Path,
        *,
        artifact_id: str,
        media_kind: str,
        local_quality: dict[str, object],
        spec: object,
        reference_root: Path | None = None,
    ) -> QualityEvaluation:
        if not self.url:
            return QualityEvaluation(
                configured=False,
                passed=None,
                score=None,
                provider="disabled",
                checks=[],
            )
        if not self.allow_data_export:
            return QualityEvaluation(
                configured=True,
                passed=None,
                score=None,
                provider="external-quality",
                checks=[],
                error=(
                    "external quality data export is disabled; set "
                    "MEDIAFORGE_QUALITY_ALLOW_DATA_EXPORT=true to enable"
                ),
            )
        spec_payload = spec.model_dump(mode="json") if hasattr(spec, "model_dump") else str(spec)
        try:
            frames = sample_frames(artifact_path, media_kind)
            reference_frames = []
            if isinstance(spec_payload, dict):
                for reference in spec_payload.get("reference_assets", []):
                    uri = reference.pop("uri", None)
                    if not uri or reference_root is None or len(reference_frames) >= 6:
                        continue
                    path = Path(uri).resolve()
                    if not path.is_relative_to(reference_root.resolve()) or not path.is_file():
                        continue
                    digest = self._sha256(path)
                    if reference.get("sha256") and reference["sha256"] != digest:
                        raise QualityEvaluationError("reference asset hash does not match")
                    reference_frames.append({"asset_id": reference["asset_id"], "character": reference.get("character"),
                                             "sha256": digest, "frames": sample_frames(path, "image")})
        except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired) as exc:
            return QualityEvaluation(configured=True, passed=None if self.fail_open else False, score=None,
                                     provider="external-quality", checks=[], error=f"visual evidence unavailable: {type(exc).__name__}")
        payload = {
            "schema_version": "mediaforge-quality-evaluation-request-v2",
            "artifact_id": artifact_id,
            "media_kind": media_kind,
            "artifact": {
                "sha256": self._sha256(artifact_path),
                "size_bytes": artifact_path.stat().st_size,
            },
            "frames": frames,
            "reference_frames": reference_frames,
            "local_quality": {k: local_quality.get(k) for k in ("passed", "checks", "quality_score")},
            "spec": spec_payload,
        }
        body = json.dumps(payload, ensure_ascii=True).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        last_error: Exception | None = None
        for attempt in range(self.retries + 1):
            try:
                request = Request(self.url, data=body, headers=headers, method="POST")
                with urlopen(request, timeout=self.timeout_seconds) as response:
                    result = json.loads(
                        response.read(1024 * 1024).decode("utf-8")
                    )
                if not isinstance(result, dict):
                    raise QualityEvaluationError("quality evaluator response must be an object")
                passed = result.get("passed")
                if not isinstance(passed, bool):
                    raise QualityEvaluationError("quality evaluator passed must be boolean")
                score = result.get("score")
                score_value = float(score) if score is not None else None
                if score_value is not None and not 0 <= score_value <= 1:
                    raise QualityEvaluationError("quality evaluator score must be between 0 and 1")
                checks = result.get("checks") or []
                if not isinstance(checks, list):
                    raise QualityEvaluationError("quality evaluator checks must be a list")
                if any(not isinstance(check, dict) or not isinstance(check.get("passed"), bool) for check in checks):
                    raise QualityEvaluationError("each quality check must contain a boolean passed")
                passed = passed and all(check["passed"] for check in checks)
                return QualityEvaluation(
                    configured=True,
                    passed=passed,
                    score=score_value,
                    provider=str(result.get("provider") or "external-quality"),
                    checks=[item for item in checks if isinstance(item, dict)],
                    error=str(result.get("error")) if result.get("error") else None,
                )
            except (HTTPError, URLError, OSError, ValueError, TypeError, json.JSONDecodeError, QualityEvaluationError) as exc:
                last_error = exc
                if attempt < self.retries:
                    time.sleep(min(2.0 ** attempt, 5.0))
        if self.fail_open:
            return QualityEvaluation(
                configured=True,
                passed=None,
                score=None,
                provider="external-quality",
                checks=[],
                error=str(last_error),
            )
        return QualityEvaluation(
            configured=True,
            passed=False,
            score=0.0,
            provider="external-quality",
            checks=[
                {
                    "name": "external_evaluator_available",
                    "passed": False,
                    "observed": str(last_error),
                    "expected": "successful evaluator response",
                }
            ],
            error=str(last_error),
        )

    @staticmethod
    def _sha256(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()
