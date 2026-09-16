from __future__ import annotations

import base64
import hashlib
import io
import subprocess
from pathlib import Path

from PIL import Image, ImageOps, ImageStat

from .media import find_ffmpeg, probe_video


def sample_frames(path: Path, media_kind: str) -> list[dict[str, object]]:
    """Bounded JPEG evidence for local diagnostics and explicitly enabled remote review."""
    timestamps = [0.0]
    if media_kind == "video":
        probe = probe_video(path)
        if not probe.valid or not probe.duration_seconds:
            raise ValueError("video cannot be decoded for visual evaluation")
        timestamps = [round(probe.duration_seconds * ratio, 3) for ratio in (0.0, 0.4, 0.8)]
    frames = []
    for timestamp in timestamps:
        if media_kind == "video":
            ffmpeg, _ = find_ffmpeg()
            result = subprocess.run(
                [ffmpeg, "-nostdin", "-v", "error", "-ss", str(timestamp), "-i", str(path),
                 "-frames:v", "1", "-vf", "scale=512:512:force_original_aspect_ratio=decrease",
                 "-f", "image2pipe", "-vcodec", "mjpeg", "pipe:1"],
                capture_output=True, timeout=20,
            )
            if result.returncode != 0 or not result.stdout or len(result.stdout) > 2 * 1024 * 1024:
                raise ValueError("video frame extraction failed")
            source = io.BytesIO(result.stdout)
        else:
            source = path
        with Image.open(source) as image:
            image = ImageOps.exif_transpose(image).convert("RGB")
            image.thumbnail((512, 512))
            gray = image.convert("L")
            stats = ImageStat.Stat(gray)
            histogram = gray.histogram()
            total = max(sum(histogram), 1)
            output = io.BytesIO()
            image.save(output, format="JPEG", quality=80)
            data = output.getvalue()
            frames.append({
                "timestamp_seconds": timestamp, "mime_type": "image/jpeg",
                "width": image.width, "height": image.height,
                "sha256": hashlib.sha256(data).hexdigest(),
                "data_b64": base64.b64encode(data).decode("ascii"),
                "mean_luma": round(stats.mean[0], 3), "contrast": round(stats.stddev[0], 3),
                "black_ratio": round(sum(histogram[:8]) / total, 4),
                "white_ratio": round(sum(histogram[248:]) / total, 4),
            })
    return frames


def visual_report(path: Path, media_kind: str, *, blocking: bool = False) -> dict[str, object]:
    try:
        frames = sample_frames(path, media_kind)
        checks = [
            {"name": "visual_exposure", "passed": all(f["black_ratio"] < 0.98 and f["white_ratio"] < 0.98 for f in frames),
             "observed": [{"time": f["timestamp_seconds"], "black": f["black_ratio"], "white": f["white_ratio"]} for f in frames],
             "expected": "black/white pixel ratio < 0.98"},
            {"name": "visual_contrast", "passed": any(f["contrast"] >= 5 for f in frames),
             "observed": [f["contrast"] for f in frames], "expected": "at least one sampled frame with contrast >= 5"},
        ]
        return {"policy_version": "sampled-visual-v1", "available": True, "blocking": blocking,
                "passed": all(check["passed"] for check in checks), "checks": checks,
                "frames": [{k: v for k, v in frame.items() if k != "data_b64"} for frame in frames],
                "identical_samples": len(frames) > 1 and len({f["sha256"] for f in frames}) == 1}
    except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired) as exc:
        return {"policy_version": "sampled-visual-v1", "available": False, "blocking": blocking,
                "passed": False, "checks": [{"name": "visual_decode", "passed": False,
                "observed": type(exc).__name__, "expected": "readable sampled frames"}], "frames": []}
