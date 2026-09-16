from __future__ import annotations

import hashlib
import mimetypes
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory

from PIL import Image, ImageStat


class FFmpegUnavailable(RuntimeError):
    """Raised when no usable FFmpeg executable can be found."""


def find_ffmpeg() -> tuple[str, str]:
    system_ffmpeg = shutil.which("ffmpeg")
    if system_ffmpeg:
        return system_ffmpeg, "system"

    try:
        import imageio_ffmpeg

        bundled_ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    except (ImportError, RuntimeError) as exc:
        raise FFmpegUnavailable(
            "FFmpeg is not on PATH and imageio-ffmpeg is unavailable"
        ) from exc

    return bundled_ffmpeg, "imageio-ffmpeg"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _probe_missing_or_empty(path: Path) -> MediaProbeResult:
    return MediaProbeResult(
        valid=False,
        path=str(path),
        duration_seconds=None,
        width=None,
        height=None,
        format=None,
        error="file is missing or empty",
    )


def _run_ffmpeg(args: list[str]) -> subprocess.CompletedProcess[str]:
    executable, _ = find_ffmpeg()
    return subprocess.run(
        [executable, *args],
        capture_output=True,
        text=True,
        check=False,
    )


def create_placeholder_video(
    output_path: Path,
    *,
    duration_seconds: float,
    color: str,
    size: str = "640x360",
) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    result = _run_ffmpeg(
        [
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            f"color=c={color}:s={size}:r=24:d={duration_seconds}",
            "-an",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            str(output_path),
        ]
    )
    if result.returncode != 0 or not output_path.exists():
        raise RuntimeError(f"FFmpeg video creation failed: {result.stderr.strip()}")
    return output_path


def create_placeholder_audio(
    output_path: Path,
    *,
    duration_seconds: float,
    frequency_hz: int = 440,
) -> Path:
    if duration_seconds <= 0:
        raise ValueError("audio duration_seconds must be > 0")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    result = _run_ffmpeg(
        [
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            f"sine=frequency={frequency_hz}:duration={duration_seconds}",
            "-c:a",
            "aac",
            "-b:a",
            "96k",
            str(output_path),
        ]
    )
    if result.returncode != 0 or not output_path.exists():
        raise RuntimeError(f"FFmpeg audio creation failed: {result.stderr.strip()}")
    return output_path


def create_video_from_image(
    image_path: Path,
    output_path: Path,
    *,
    duration_seconds: float,
    size: str = "640x360",
) -> Path:
    width, height = size.split("x", 1)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    result = _run_ffmpeg(
        [
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-loop",
            "1",
            "-i",
            str(image_path),
            "-t",
            str(duration_seconds),
            "-r",
            "24",
            "-vf",
            f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
            f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,"
            "format=yuv420p",
            "-an",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            str(output_path),
        ]
    )
    if result.returncode != 0 or not output_path.exists():
        raise RuntimeError(
            f"FFmpeg image-to-video conversion failed: {result.stderr.strip()}"
        )
    return output_path


@dataclass(frozen=True)
class MediaProbeResult:
    valid: bool
    path: str
    duration_seconds: float | None
    width: int | None
    height: int | None
    format: str | None = None
    error: str | None = None


def probe_image(path: Path) -> MediaProbeResult:
    if not path.exists() or path.stat().st_size == 0:
        return _probe_missing_or_empty(path)

    try:
        with Image.open(path) as image:
            image.verify()
        with Image.open(path) as image:
            width, height = image.size
            return MediaProbeResult(
                valid=True,
                path=str(path),
                duration_seconds=None,
                width=width,
                height=height,
                format=image.format,
                error=None,
            )
    except Exception as exc:
        return MediaProbeResult(
            valid=False,
            path=str(path),
            duration_seconds=None,
            width=None,
            height=None,
            format=None,
            error=str(exc),
        )


def inspect_image_visual_signal(path: Path) -> dict[str, object]:
    """Return lightweight, explainable visual signals for quality review."""
    try:
        with Image.open(path) as image:
            image = image.convert("L")
            image.thumbnail((256, 256))
            stats = ImageStat.Stat(image)
            pixels = list(image.getdata())
            total = len(pixels) or 1
            active = sum(1 for value in pixels if 8 <= value <= 247)
            mean = round(float(stats.mean[0]), 3)
            contrast = round(float(stats.stddev[0]), 3)
            active_ratio = round(active / total, 4)
            return {
                "available": True,
                "mean_luma": mean,
                "contrast": contrast,
                "active_pixel_ratio": active_ratio,
                "signal": "strong" if contrast >= 12 and active_ratio >= 0.05 else "weak",
                "error": None,
            }
    except Exception as exc:
        return {
            "available": False,
            "mean_luma": None,
            "contrast": None,
            "active_pixel_ratio": None,
            "signal": "unavailable",
            "error": str(exc),
        }


def probe_video(path: Path) -> MediaProbeResult:
    if not path.exists() or path.stat().st_size == 0:
        return _probe_missing_or_empty(path)

    try:
        result = _run_ffmpeg(
            [
                "-hide_banner",
                "-i",
                str(path),
                "-f",
                "null",
                "-",
            ]
        )
    except FFmpegUnavailable as exc:
        return MediaProbeResult(
            valid=False,
            path=str(path),
            duration_seconds=None,
            width=None,
            height=None,
            format=None,
            error=str(exc),
        )

    diagnostics = f"{result.stdout}\n{result.stderr}"
    duration_match = re.search(
        r"Duration:\s+(\d+):(\d+):(\d+(?:\.\d+)?)",
        diagnostics,
    )
    size_match = re.search(r"(\d{2,5})x(\d{2,5})", diagnostics)
    duration = None
    if duration_match:
        hours, minutes, seconds = duration_match.groups()
        duration = int(hours) * 3600 + int(minutes) * 60 + float(seconds)
    width = int(size_match.group(1)) if size_match else None
    height = int(size_match.group(2)) if size_match else None
    valid = result.returncode == 0 and duration is not None and width and height

    return MediaProbeResult(
        valid=bool(valid),
        path=str(path),
        duration_seconds=duration,
        width=width,
        height=height,
        format=None,
        error=None if valid else diagnostics.strip()[-500:],
    )


def probe_audio(path: Path) -> MediaProbeResult:
    if not path.exists() or path.stat().st_size == 0:
        return _probe_missing_or_empty(path)

    try:
        result = _run_ffmpeg(
            [
                "-hide_banner",
                "-i",
                str(path),
                "-map",
                "0:a:0",
                "-f",
                "null",
                "-",
            ]
        )
    except FFmpegUnavailable as exc:
        return MediaProbeResult(
            valid=False,
            path=str(path),
            duration_seconds=None,
            width=None,
            height=None,
            format=None,
            error=str(exc),
        )
    diagnostics = f"{result.stdout}\n{result.stderr}"
    duration_match = re.search(
        r"Duration:\s+(\d{2}):(\d{2}):(\d{2}(?:\.\d+)?)",
        diagnostics,
    )
    duration = None
    if duration_match:
        hours, minutes, seconds = duration_match.groups()
        duration = int(hours) * 3600 + int(minutes) * 60 + float(seconds)
    valid = result.returncode == 0 and duration is not None
    return MediaProbeResult(
        valid=bool(valid),
        path=str(path),
        duration_seconds=duration,
        width=None,
        height=None,
        format=path.suffix.lower().lstrip(".") or None,
        error=None if valid else diagnostics.strip()[-500:],
    )


def create_image_thumbnail(
    source_path: Path,
    output_path: Path,
    *,
    maximum_size: tuple[int, int] = (512, 512),
) -> Path:
    """Create a deterministic PNG thumbnail without altering the source file."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with Image.open(source_path) as image:
        image.thumbnail(maximum_size)
        if image.mode not in {"RGB", "RGBA"}:
            image = image.convert("RGBA" if "A" in image.getbands() else "RGB")
        image.save(output_path, format="PNG", optimize=True)
    if not output_path.is_file() or output_path.stat().st_size == 0:
        raise RuntimeError("image thumbnail creation failed")
    return output_path


def create_video_frame(
    source_path: Path,
    output_path: Path,
    *,
    at_seconds: float = 0.0,
    maximum_width: int = 960,
) -> Path:
    if at_seconds < 0:
        raise ValueError("video frame timestamp must be >= 0")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    result = _run_ffmpeg(
        [
            "-y", "-hide_banner", "-loglevel", "error",
            "-ss", f"{at_seconds:.3f}", "-i", str(source_path),
            "-frames:v", "1",
            "-vf", f"scale={maximum_width}:-2:force_original_aspect_ratio=decrease",
            str(output_path),
        ]
    )
    if result.returncode != 0 or not output_path.is_file():
        raise RuntimeError(f"FFmpeg video frame creation failed: {result.stderr.strip()}")
    return output_path


def create_video_proxy(
    source_path: Path,
    output_path: Path,
    *,
    maximum_width: int = 1280,
) -> Path:
    """Create an H.264/AAC edit proxy suitable for browser previews."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    result = _run_ffmpeg(
        [
            "-y", "-hide_banner", "-loglevel", "error", "-i", str(source_path),
            "-vf", f"scale=min({maximum_width}\\,iw):-2",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "25",
            "-c:a", "aac", "-b:a", "96k", "-movflags", "+faststart",
            str(output_path),
        ]
    )
    if result.returncode != 0 or not output_path.is_file():
        raise RuntimeError(f"FFmpeg proxy creation failed: {result.stderr.strip()}")
    return output_path


def create_audio_waveform(
    source_path: Path,
    output_path: Path,
    *,
    width: int = 1200,
    height: int = 240,
) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    result = _run_ffmpeg(
        [
            "-y", "-hide_banner", "-loglevel", "error", "-i", str(source_path),
            "-filter_complex", f"aformat=channel_layouts=mono,showwavespic=s={width}x{height}:colors=169b84",
            "-frames:v", "1", str(output_path),
        ]
    )
    if result.returncode != 0 or not output_path.is_file():
        raise RuntimeError(f"FFmpeg waveform creation failed: {result.stderr.strip()}")
    return output_path


def create_video_contact_sheet(
    source_path: Path,
    output_path: Path,
    *,
    duration_seconds: float | None,
    columns: int = 4,
    frame_count: int = 8,
) -> Path:
    """Compose representative video frames into a review contact sheet."""
    if columns < 1 or frame_count < 1:
        raise ValueError("contact sheet dimensions must be positive")
    duration = max(float(duration_seconds or 0), 0.1)
    timestamps = [duration * index / max(frame_count - 1, 1) for index in range(frame_count)]
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix="mediaforge-contact-sheet-") as temporary:
        frame_paths: list[Path] = []
        for index, timestamp in enumerate(timestamps):
            frame_path = Path(temporary) / f"frame-{index:02d}.png"
            create_video_frame(source_path, frame_path, at_seconds=timestamp, maximum_width=360)
            frame_paths.append(frame_path)
        with Image.open(frame_paths[0]) as first:
            cell_width, cell_height = first.size
        rows = (len(frame_paths) + columns - 1) // columns
        sheet = Image.new("RGB", (cell_width * columns, cell_height * rows), "#111827")
        for index, frame_path in enumerate(frame_paths):
            with Image.open(frame_path) as frame:
                sheet.paste(frame.convert("RGB"), ((index % columns) * cell_width, (index // columns) * cell_height))
        sheet.save(output_path, format="JPEG", quality=88, optimize=True)
    if not output_path.is_file() or output_path.stat().st_size == 0:
        raise RuntimeError("video contact sheet creation failed")
    return output_path


def create_media_derivatives(
    source_path: Path,
    *,
    media_kind: str,
    output_directory: Path,
    duration_seconds: float | None = None,
) -> list[dict[str, object]]:
    """Create review/edit derivatives and return portable inventory records.

    The caller owns persistence.  Failures are represented in the resulting
    record so an optional derivative never makes a source asset unusable.
    """
    if not source_path.is_file():
        raise ValueError(f"source media is missing: {source_path}")
    output_directory.mkdir(parents=True, exist_ok=True)
    source_hash = sha256_file(source_path)
    records: list[dict[str, object]] = []

    def add(kind: str, path: Path, creator) -> None:
        try:
            created = creator()
            records.append(
                {
                    "derivative_id": f"{source_hash[:16]}:{kind}",
                    "kind": kind,
                    "uri": str(created),
                    "mime_type": mimetypes.guess_type(created.name)[0] or "application/octet-stream",
                    "sha256": sha256_file(created),
                    "size_bytes": created.stat().st_size,
                    "source_sha256": source_hash,
                    "available": True,
                }
            )
        except (OSError, RuntimeError, ValueError, FFmpegUnavailable) as exc:
            records.append(
                {
                    "derivative_id": f"{source_hash[:16]}:{kind}",
                    "kind": kind,
                    "uri": None,
                    "mime_type": None,
                    "sha256": None,
                    "size_bytes": None,
                    "source_sha256": source_hash,
                    "available": False,
                    "error": str(exc),
                }
            )

    if media_kind == "image":
        add("thumbnail", output_directory / "thumbnail.png", lambda: create_image_thumbnail(source_path, output_directory / "thumbnail.png"))
    elif media_kind == "video":
        add("thumbnail", output_directory / "thumbnail.png", lambda: create_video_frame(source_path, output_directory / "thumbnail.png", at_seconds=min(max(float(duration_seconds or 0) * 0.2, 0), 2)))
        add("proxy", output_directory / "proxy.mp4", lambda: create_video_proxy(source_path, output_directory / "proxy.mp4"))
        add("contact_sheet", output_directory / "contact-sheet.jpg", lambda: create_video_contact_sheet(source_path, output_directory / "contact-sheet.jpg", duration_seconds=duration_seconds))
    elif media_kind in {"audio", "audio_track"}:
        add("waveform", output_directory / "waveform.png", lambda: create_audio_waveform(source_path, output_directory / "waveform.png"))
    else:
        raise ValueError(f"unsupported derivative media kind: {media_kind}")
    return records


def concat_videos(video_paths: list[Path], output_path: Path) -> Path:
    if not video_paths:
        raise ValueError("at least one video is required")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    list_path = output_path.with_suffix(".concat.txt")
    list_path.write_text(
        "".join(f"file '{path.resolve().as_posix()}'\n" for path in video_paths),
        encoding="utf-8",
    )
    try:
        result = _run_ffmpeg(
            [
                "-y",
                "-hide_banner",
                "-loglevel",
                "error",
                "-f",
                "concat",
                "-safe",
                "0",
                "-i",
                str(list_path),
                "-c",
                "copy",
                str(output_path),
            ]
        )
    finally:
        list_path.unlink(missing_ok=True)

    if result.returncode != 0 or not output_path.exists():
        raise RuntimeError(f"FFmpeg concatenation failed: {result.stderr.strip()}")
    return output_path


def mix_audio(
    video_path: Path,
    audio_path: Path,
    output_path: Path,
) -> Path:
    if not video_path.is_file():
        raise ValueError(f"video input is missing: {video_path}")
    if not audio_path.is_file():
        raise ValueError(f"audio input is missing: {audio_path}")
    audio_probe = probe_audio(audio_path)
    if not audio_probe.valid:
        raise ValueError(
            f"audio input is invalid: {audio_probe.error or audio_path}"
        )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    result = _run_ffmpeg(
        [
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            str(video_path),
            "-stream_loop",
            "-1",
            "-i",
            str(audio_path),
            "-map",
            "0:v:0",
            "-map",
            "1:a:0",
            "-c:v",
            "copy",
            "-c:a",
            "aac",
            "-b:a",
            "128k",
            "-shortest",
            "-movflags",
            "+faststart",
            str(output_path),
        ]
    )
    if result.returncode != 0 or not output_path.exists():
        raise RuntimeError(f"FFmpeg audio mix failed: {result.stderr.strip()}")
    return output_path


def render_timed_audio(
    segments: list[tuple[Path, float, float]],
    output_path: Path,
    *,
    duration_seconds: float,
) -> Path:
    """Place normalized speech segments on a bounded timeline and mix them."""
    if not segments:
        raise ValueError("at least one timed audio segment is required")
    if duration_seconds <= 0:
        raise ValueError("timeline duration must be positive")
    if any(not path.is_file() for path, _, _ in segments):
        raise ValueError("timed audio segment is missing")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    filters: list[str] = []
    labels: list[str] = []
    for index, (_, start, end) in enumerate(segments):
        length = end - start
        if start < 0 or length <= 0 or end > duration_seconds + 0.001:
            raise ValueError("timed audio segment is outside the project timeline")
        label = f"speech{index}"
        # Trim each line to its declared cue, then pad the mixed result to the project end.
        filters.append(
            f"[{index}:a]atrim=duration={length:.3f},asetpts=PTS-STARTPTS,"
            f"adelay={round(start * 1000)}:all=1,apad,atrim=duration={duration_seconds:.3f}[{label}]"
        )
        labels.append(f"[{label}]")
    filters.append(
        f"{''.join(labels)}amix=inputs={len(labels)}:duration=longest:"
        f"dropout_transition=0:normalize=0,atrim=duration={duration_seconds:.3f}[mixed]"
    )
    result = _run_ffmpeg(
        [
            "-y", "-hide_banner", "-loglevel", "error",
            *sum((["-i", str(path)] for path, _, _ in segments), []),
            "-filter_complex", ";".join(filters),
            "-map", "[mixed]", "-c:a", "aac", "-b:a", "128k",
            "-t", f"{duration_seconds:.3f}", "-movflags", "+faststart",
            str(output_path),
        ]
    )
    if result.returncode != 0 or not output_path.is_file():
        raise RuntimeError(f"FFmpeg timed audio render failed: {result.stderr.strip()}")
    return output_path


@dataclass(frozen=True)
class SubtitleCue:
    start_seconds: float
    end_seconds: float
    text: str

    def __post_init__(self) -> None:
        if self.start_seconds < 0:
            raise ValueError("subtitle cue start_seconds must be >= 0")
        if self.end_seconds <= self.start_seconds:
            raise ValueError("subtitle cue end_seconds must be after start_seconds")
        if not self.text.strip():
            raise ValueError("subtitle cue text is required")


def _srt_timestamp(seconds: float) -> str:
    milliseconds = max(0, int(round(seconds * 1000)))
    hours, remainder = divmod(milliseconds, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    whole_seconds, millis = divmod(remainder, 1000)
    return f"{hours:02d}:{minutes:02d}:{whole_seconds:02d},{millis:03d}"


def write_srt(output_path: Path, cues: list[SubtitleCue]) -> Path:
    if not cues:
        raise ValueError("at least one subtitle cue is required")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    rows: list[str] = []
    for index, cue in enumerate(cues, start=1):
        text = cue.text.replace("\r\n", "\n").replace("\r", "\n").strip()
        rows.extend(
            [
                str(index),
                f"{_srt_timestamp(cue.start_seconds)} --> {_srt_timestamp(cue.end_seconds)}",
                text,
                "",
            ]
        )
    output_path.write_text("\n".join(rows), encoding="utf-8")
    return output_path
