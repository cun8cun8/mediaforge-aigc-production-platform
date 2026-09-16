from __future__ import annotations

import base64
import json
import os
import shlex
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter
from urllib.request import Request, urlopen
from uuid import uuid4

from .media import probe_video, sha256_file


class LipSyncError(RuntimeError):
    pass


def _command_args(command: str) -> list[str]:
    try:
        args = shlex.split(command, posix=False)
    except ValueError as exc:
        raise LipSyncError(f"invalid lip-sync command: {exc}") from exc
    normalized = [item[1:-1] if len(item) >= 2 and item[0] == item[-1] and item[0] in {'"', "'"} else item for item in args]
    if not normalized:
        raise LipSyncError("lip-sync command is empty")
    return normalized


@dataclass(frozen=True)
class LipSyncSettings:
    mode: str
    command: str
    url: str
    timeout_seconds: float
    allow_data_export: bool

    @classmethod
    def from_env(cls) -> "LipSyncSettings":
        mode = os.getenv("MEDIAFORGE_LIPSYNC_MODE", "disabled").strip().lower()
        if mode not in {"disabled", "command", "http"}:
            raise LipSyncError("MEDIAFORGE_LIPSYNC_MODE must be disabled, command or http")
        try:
            timeout = float(os.getenv("MEDIAFORGE_LIPSYNC_TIMEOUT_SECONDS", "300"))
        except ValueError as exc:
            raise LipSyncError("MEDIAFORGE_LIPSYNC_TIMEOUT_SECONDS must be a number") from exc
        if timeout <= 0:
            raise LipSyncError("MEDIAFORGE_LIPSYNC_TIMEOUT_SECONDS must be > 0")
        return cls(
            mode=mode,
            command=os.getenv("MEDIAFORGE_LIPSYNC_COMMAND", "").strip(),
            url=os.getenv("MEDIAFORGE_LIPSYNC_URL", "").strip(),
            timeout_seconds=timeout,
            allow_data_export=os.getenv("MEDIAFORGE_LIPSYNC_ALLOW_DATA_EXPORT", "false").strip().lower() in {"1", "true", "yes", "on"},
        )


class LipSyncAdapter:
    """Provider-neutral lip-sync boundary for local GPU commands or HTTP workers."""

    name = "lip-sync-adapter"

    def __init__(self, settings: LipSyncSettings) -> None:
        self.settings = settings

    def status_view(self) -> dict[str, object]:
        target_configured = bool(self.settings.command) if self.settings.mode == "command" else bool(self.settings.url) if self.settings.mode == "http" else True
        configured = self.settings.mode == "disabled" or target_configured
        return {
            "mode": self.settings.mode,
            "configured": configured,
            "provider": self.name,
            "message": "口型同步适配器已关闭。" if self.settings.mode == "disabled" else ("口型同步目标已配置。" if configured else "口型同步目标未配置。"),
            "details": {
                "command_configured": bool(self.settings.command),
                "url_configured": bool(self.settings.url),
                "allow_data_export": self.settings.allow_data_export,
                "timeout_seconds": self.settings.timeout_seconds,
            },
        }

    def render(self, video_path: Path, audio_path: Path, output_path: Path, *, metadata: dict[str, object]) -> dict[str, object]:
        if self.settings.mode == "disabled":
            raise LipSyncError("lip-sync adapter is disabled")
        if not video_path.is_file() or not audio_path.is_file():
            raise LipSyncError("lip-sync input video or audio is missing")
        output_path.parent.mkdir(parents=True, exist_ok=True)
        if self.settings.mode == "command":
            self._render_command(video_path, audio_path, output_path, metadata)
        else:
            self._render_http(video_path, audio_path, output_path, metadata)
        probe = probe_video(output_path)
        if not probe.valid:
            raise LipSyncError(f"lip-sync output is not a readable video: {probe.error or 'invalid media'}")
        return {
            "provider": self.name,
            "mode": self.settings.mode,
            "uri": str(output_path),
            "sha256": sha256_file(output_path),
            "size_bytes": output_path.stat().st_size,
            "duration_seconds": probe.duration_seconds,
            "width": probe.width,
            "height": probe.height,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }

    def _render_command(self, video_path: Path, audio_path: Path, output_path: Path, metadata: dict[str, object]) -> None:
        args = _command_args(self.settings.command)
        request_path = output_path.parent / f".lipsync-request-{uuid4().hex}.json"
        payload = {"video_path": str(video_path), "audio_path": str(audio_path), "output_path": str(output_path), **metadata}
        request_path.write_text(json.dumps(payload, ensure_ascii=True, indent=2), encoding="utf-8")
        env = os.environ.copy()
        env.update({"MEDIAFORGE_VIDEO_PATH": str(video_path), "MEDIAFORGE_AUDIO_PATH": str(audio_path), "MEDIAFORGE_OUTPUT_PATH": str(output_path), "MEDIAFORGE_REQUEST_PATH": str(request_path)})
        started = perf_counter()
        try:
            result = subprocess.run(args, input=json.dumps(payload, ensure_ascii=True), text=True, capture_output=True, timeout=self.settings.timeout_seconds, env=env, cwd=str(output_path.parent), check=False)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise LipSyncError(f"lip-sync command failed after {perf_counter() - started:.1f}s: {exc}") from exc
        finally:
            request_path.unlink(missing_ok=True)
        if result.returncode != 0:
            message = (result.stderr or result.stdout or "command returned a non-zero exit code").strip()[-2000:]
            raise LipSyncError(f"lip-sync command failed: {message}")
        if not output_path.is_file():
            raise LipSyncError("lip-sync command completed without creating MEDIAFORGE_OUTPUT_PATH")

    def _render_http(self, video_path: Path, audio_path: Path, output_path: Path, metadata: dict[str, object]) -> None:
        if not self.settings.allow_data_export:
            raise LipSyncError("HTTP lip-sync requires MEDIAFORGE_LIPSYNC_ALLOW_DATA_EXPORT=true")
        payload = {
            "video_filename": video_path.name,
            "audio_filename": audio_path.name,
            "video_base64": base64.b64encode(video_path.read_bytes()).decode("ascii"),
            "audio_base64": base64.b64encode(audio_path.read_bytes()).decode("ascii"),
            **metadata,
        }
        request = Request(self.settings.url, data=json.dumps(payload, ensure_ascii=True).encode("utf-8"), headers={"Content-Type": "application/json", "Accept": "application/json"}, method="POST")
        try:
            with urlopen(request, timeout=self.settings.timeout_seconds) as response:
                result = json.loads(response.read().decode("utf-8"))
        except Exception as exc:  # pragma: no cover - external boundary
            raise LipSyncError(f"HTTP lip-sync request failed: {exc}") from exc
        output_b64 = result.get("output_base64") if isinstance(result, dict) else None
        if not isinstance(output_b64, str) or not output_b64.strip():
            raise LipSyncError("HTTP lip-sync response must include output_base64")
        try:
            output_path.write_bytes(base64.b64decode(output_b64, validate=True))
        except (ValueError, OSError) as exc:
            raise LipSyncError("HTTP lip-sync output_base64 is invalid") from exc


def build_lipsync_from_env() -> LipSyncAdapter:
    return LipSyncAdapter(LipSyncSettings.from_env())
