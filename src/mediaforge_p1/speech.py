from __future__ import annotations

import base64
import json
import math
import os
import shutil
import subprocess
from tempfile import TemporaryDirectory
from pathlib import Path
from urllib.request import Request, urlopen

from .media import create_placeholder_audio, find_ffmpeg, probe_audio


class SpeechSynthesisError(RuntimeError):
    pass


class SpeechSynthesizer:
    """TTS adapter with a deterministic local preview and a JSON HTTP provider."""

    def __init__(self, *, mode: str = "deterministic", url: str = "", token: str = "", timeout: float = 30.0) -> None:
        self.mode = mode.strip().lower() or "deterministic"
        self.url = url.strip()
        self.token = token.strip()
        if self.mode not in {"deterministic", "system", "http"}:
            raise SpeechSynthesisError("TTS mode must be deterministic, system or http")
        if not math.isfinite(timeout) or timeout <= 0:
            raise SpeechSynthesisError("TTS timeout must be finite and positive")
        self.timeout = float(timeout)

    @classmethod
    def from_env(cls) -> "SpeechSynthesizer":
        try:
            timeout = float(os.getenv("MEDIAFORGE_TTS_TIMEOUT_SECONDS", "30"))
        except ValueError as exc:
            raise SpeechSynthesisError("MEDIAFORGE_TTS_TIMEOUT_SECONDS must be a number") from exc
        return cls(
            mode=os.getenv("MEDIAFORGE_TTS_MODE", "system" if os.name == "nt" else "deterministic"),
            url=os.getenv("MEDIAFORGE_TTS_URL", ""),
            token=os.getenv("MEDIAFORGE_TTS_TOKEN", ""),
            timeout=timeout,
        )

    @staticmethod
    def _system_engine() -> str | None:
        if os.name != "nt":
            return None
        executable = shutil.which("powershell")
        if executable:
            return executable
        standard = Path(os.environ.get("SystemRoot", "C:/Windows")) / "System32/WindowsPowerShell/v1.0/powershell.exe"
        return str(standard) if standard.is_file() else shutil.which("pwsh")

    def status_view(self) -> dict[str, object]:
        system_ready = self.mode == "system" and bool(self._system_engine())
        configured = self.mode == "deterministic" or (self.mode == "system" and system_ready) or (self.mode == "http" and bool(self.url))
        return {"mode": self.mode, "configured": configured, "system_ready": system_ready, "url": self.url or None, "token_configured": bool(self.token), "preview_only": self.mode == "deterministic"}

    def synthesize(self, text: str, output_path: Path, *, voice: str = "default", language: str = "zh-CN") -> dict[str, object]:
        clean_text = " ".join(text.split())
        if not clean_text or len(clean_text) > 12000:
            raise SpeechSynthesisError("voiceover text must contain 1 to 12000 characters")
        output_path.parent.mkdir(parents=True, exist_ok=True)
        # Never expose incomplete output or replace a previously accepted track on failure.
        try:
            with TemporaryDirectory(prefix=".tts-", dir=output_path.parent) as folder:
                raw = Path(folder) / ("source.m4a" if self.mode == "deterministic" else "source.wav")
                metadata = self._synthesize_raw(clean_text, raw, voice=voice, language=language)
                normalized = Path(folder) / "normalized.m4a"
                ffmpeg, _ = find_ffmpeg()
                result = subprocess.run(
                    [ffmpeg, "-nostdin", "-y", "-v", "error", "-i", str(raw),
                     "-map", "0:a:0", "-vn", "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart", str(normalized)],
                    capture_output=True, timeout=self.timeout,
                )
                if result.returncode != 0 or not probe_audio(normalized).valid:
                    raise SpeechSynthesisError("TTS provider returned unreadable audio")
                normalized.replace(output_path)
                return metadata
        except SpeechSynthesisError:
            raise
        except (OSError, RuntimeError, subprocess.TimeoutExpired) as exc:
            raise SpeechSynthesisError("speech synthesis or audio normalization failed") from exc

    def _synthesize_raw(self, clean_text: str, output_path: Path, *, voice: str, language: str) -> dict[str, object]:
        if self.mode == "deterministic":
            duration = max(1.0, min(120.0, len(clean_text) / 4.5))
            create_placeholder_audio(output_path, duration_seconds=duration, frequency_hz=440)
            return {"provider": "deterministic-voice-preview", "voice": voice, "language": language, "text_length": len(clean_text), "preview_only": True}
        if self.mode == "system":
            executable = self._system_engine()
            if not executable:
                raise SpeechSynthesisError("system speech engine is unavailable; configure MEDIAFORGE_TTS_MODE=http")
            script = """
$ErrorActionPreference='Stop'
$speaker=New-Object -ComObject SAPI.SpVoice
$lcid=[System.Globalization.CultureInfo]::GetCultureInfo($env:MEDIAFORGE_TTS_LANGUAGE).LCID.ToString('x')
$voices=$speaker.GetVoices('Language=' + $lcid)
$match=$voices | Where-Object { $env:MEDIAFORGE_TTS_VOICE -eq 'default' -or $_.GetDescription().IndexOf($env:MEDIAFORGE_TTS_VOICE, [System.StringComparison]::OrdinalIgnoreCase) -ge 0 } | Select-Object -First 1
if (-not $match) { throw 'No matching installed speech voice and language' }
$speaker.Voice=$match
$stream=New-Object -ComObject SAPI.SpFileStream
$stream.Format.Type=22
$stream.Open($env:MEDIAFORGE_TTS_OUTPUT, 3, $false)
try { $speaker.AudioOutputStream=$stream; [void]$speaker.Speak($env:MEDIAFORGE_TTS_TEXT) } finally { $stream.Close() }
"""
            environment = os.environ.copy()
            environment.update({"MEDIAFORGE_TTS_TEXT": clean_text, "MEDIAFORGE_TTS_OUTPUT": str(output_path), "MEDIAFORGE_TTS_VOICE": voice, "MEDIAFORGE_TTS_LANGUAGE": language})
            result = subprocess.run([executable, "-NoProfile", "-NonInteractive", "-Command", script], env=environment, capture_output=True, text=True, timeout=self.timeout)
            if result.returncode != 0 or not output_path.is_file():
                raise SpeechSynthesisError("system speech synthesis failed; check installed voice and language")
            return {"provider": "system-sapi", "voice": voice, "language": language, "text_length": len(clean_text), "preview_only": False}
        if self.mode != "http" or not self.url:
            raise SpeechSynthesisError("TTS provider is not configured")
        payload = json.dumps({"text": clean_text, "voice": voice, "language": language}, ensure_ascii=True).encode("utf-8")
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        try:
            with urlopen(Request(self.url, data=payload, headers=headers, method="POST"), timeout=self.timeout) as response:
                raw_body = response.read(50 * 1024 * 1024 + 1)
                if len(raw_body) > 50 * 1024 * 1024:
                    raise SpeechSynthesisError("TTS response exceeds 50 MiB")
                body = json.loads(raw_body.decode("utf-8"))
            audio_b64 = body.get("audio_b64") if isinstance(body, dict) else None
            if not isinstance(audio_b64, str) or not audio_b64:
                raise SpeechSynthesisError("TTS response must contain audio_b64")
            output_path.write_bytes(base64.b64decode(audio_b64, validate=True))
        except SpeechSynthesisError:
            raise
        except Exception as exc:
            raise SpeechSynthesisError("TTS request failed or returned invalid audio data") from exc
        return {"provider": "http-tts", "voice": voice, "language": language, "text_length": len(clean_text), "preview_only": False}
