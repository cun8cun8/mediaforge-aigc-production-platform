from __future__ import annotations

import base64
import io
import json
import os
import re
import subprocess
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen
from xml.etree import ElementTree


PARSER_VERSION = "mediaforge-source-parser-v2"
MAX_SOURCE_DOCUMENT_BYTES = 20 * 1024 * 1024
MAX_CHAPTER_CHARS = 120_000
MAX_OCR_TEXT_BYTES = 1_000_000
SUPPORTED_SOURCE_SUFFIXES = {".txt", ".md", ".docx", ".pdf"}


class SourceIngestError(ValueError):
    pass


@dataclass(frozen=True)
class ParsedSourceChapter:
    title: str
    content: str
    locator: str


@dataclass(frozen=True)
class SourceExtraction:
    method: str
    processor: str | None = None


@dataclass(frozen=True)
class OcrSettings:
    mode: str = "disabled"
    command: tuple[str, ...] = ()
    url: str | None = None
    timeout_seconds: int = 30

    @classmethod
    def from_env(cls) -> "OcrSettings":
        mode = os.getenv("MEDIAFORGE_OCR_MODE", "disabled").strip().lower()
        if mode not in {"disabled", "command", "http"}:
            raise SourceIngestError(
                "MEDIAFORGE_OCR_MODE must be disabled, command, or http"
            )
        timeout_value = os.getenv("MEDIAFORGE_OCR_TIMEOUT_SECONDS", "30").strip()
        try:
            timeout_seconds = int(timeout_value)
        except ValueError as exc:
            raise SourceIngestError(
                "MEDIAFORGE_OCR_TIMEOUT_SECONDS must be an integer"
            ) from exc
        if not 1 <= timeout_seconds <= 120:
            raise SourceIngestError(
                "MEDIAFORGE_OCR_TIMEOUT_SECONDS must be between 1 and 120"
            )
        if mode == "disabled":
            return cls(mode=mode, timeout_seconds=timeout_seconds)
        if mode == "http":
            url = os.getenv("MEDIAFORGE_OCR_URL", "").strip()
            parsed = urlparse(url)
            if parsed.scheme not in {"http", "https"} or not parsed.netloc:
                raise SourceIngestError(
                    "MEDIAFORGE_OCR_URL must be an HTTP(S) URL when OCR mode is http"
                )
            return cls(mode=mode, url=url, timeout_seconds=timeout_seconds)
        raw_command = os.getenv("MEDIAFORGE_OCR_COMMAND", "").strip()
        try:
            command = json.loads(raw_command)
        except json.JSONDecodeError as exc:
            raise SourceIngestError(
                "MEDIAFORGE_OCR_COMMAND must be a JSON argv array when OCR mode is command"
            ) from exc
        if (
            not isinstance(command, list)
            or not command
            or any(not isinstance(part, str) or not part for part in command)
            or "{input}" not in command
            or "{output}" not in command
        ):
            raise SourceIngestError(
                "MEDIAFORGE_OCR_COMMAND must include {input} and {output} as JSON argv items"
            )
        return cls(mode=mode, command=tuple(command), timeout_seconds=timeout_seconds)


def ocr_status() -> dict[str, object]:
    """Return a deliberately non-secret OCR readiness view for the Studio."""
    try:
        settings = OcrSettings.from_env()
    except SourceIngestError as exc:
        return {
            "configured": False,
            "mode": "invalid",
            "requires_external_processing_consent": True,
            "configuration_error": str(exc),
        }
    return {
        "configured": settings.mode != "disabled",
        "mode": settings.mode,
        "requires_external_processing_consent": True,
        "configuration_error": None,
    }


def parse_source_document(
    name: str,
    content: bytes,
    *,
    allow_external_ocr: bool = False,
) -> tuple[str, list[ParsedSourceChapter], SourceExtraction]:
    clean_name = Path(name).name
    suffix = Path(clean_name).suffix.lower()
    if suffix not in SUPPORTED_SOURCE_SUFFIXES:
        raise SourceIngestError("source document must be TXT, Markdown, DOCX, or PDF")
    if not content:
        raise SourceIngestError("source document is empty")
    if len(content) > MAX_SOURCE_DOCUMENT_BYTES:
        raise SourceIngestError("source document exceeds the 20 MB limit")
    if suffix in {".txt", ".md"}:
        text = _decode_text(content)
        extraction = SourceExtraction("native_text")
    elif suffix == ".docx":
        text = _extract_docx(content)
        extraction = SourceExtraction("native_text")
    else:
        text = _extract_pdf(content)
        extraction = SourceExtraction("native_text")
        if not text.strip():
            if not allow_external_ocr:
                raise SourceIngestError(
                    "source PDF contains no extractable text; set allow_external_processing=true to use configured OCR"
                )
            text, extraction = _extract_pdf_with_ocr(clean_name, content)
    return suffix.lstrip("."), _split_chapters(text, Path(clean_name).stem), extraction


def _decode_text(content: bytes) -> str:
    for encoding in ("utf-8-sig", "utf-8", "gb18030"):
        try:
            return content.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise SourceIngestError("source text must use UTF-8 or GB18030 encoding")


def _extract_docx(content: bytes) -> str:
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            document = archive.read("word/document.xml")
        root = ElementTree.fromstring(document)
    except (KeyError, OSError, zipfile.BadZipFile, ElementTree.ParseError) as exc:
        raise SourceIngestError("source DOCX is unreadable") from exc
    namespace = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
    paragraphs = []
    for paragraph in root.iter(f"{namespace}p"):
        value = "".join(node.text or "" for node in paragraph.iter(f"{namespace}t")).strip()
        if value:
            paragraphs.append(value)
    return "\n\n".join(paragraphs)


def _extract_pdf(content: bytes) -> str:
    try:
        from pypdf import PdfReader

        reader = PdfReader(io.BytesIO(content))
        if reader.is_encrypted:
            if reader.decrypt("") == 0:
                raise SourceIngestError("encrypted PDF is not supported")
        if len(reader.pages) > 500:
            raise SourceIngestError("source PDF exceeds the 500 page limit")
        pages = [page.extract_text() or "" for page in reader.pages]
    except SourceIngestError:
        raise
    except Exception as exc:
        raise SourceIngestError("source PDF is unreadable") from exc
    return "\n\n".join(pages)


def _extract_pdf_with_ocr(name: str, content: bytes) -> tuple[str, SourceExtraction]:
    try:
        settings = OcrSettings.from_env()
    except SourceIngestError:
        raise
    if settings.mode == "disabled":
        raise SourceIngestError(
            "source PDF contains no extractable text; configure OCR or import a text-extractable PDF"
        )
    if settings.mode == "http":
        text = _extract_pdf_with_http_ocr(name, content, settings)
        return text, SourceExtraction("ocr_http", "http")
    text = _extract_pdf_with_command_ocr(content, settings)
    return text, SourceExtraction("ocr_command", "command")


def _extract_pdf_with_http_ocr(name: str, content: bytes, settings: OcrSettings) -> str:
    payload = json.dumps(
        {
            "filename": Path(name).name,
            "mime_type": "application/pdf",
            "content_b64": base64.b64encode(content).decode("ascii"),
        }
    ).encode("utf-8")
    request = Request(
        settings.url or "",
        data=payload,
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=settings.timeout_seconds) as response:
            raw = response.read(MAX_OCR_TEXT_BYTES + 1)
    except (HTTPError, URLError, OSError) as exc:
        raise SourceIngestError("OCR service did not return readable text") from exc
    if len(raw) > MAX_OCR_TEXT_BYTES:
        raise SourceIngestError("OCR service response exceeds the text limit")
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise SourceIngestError("OCR service returned an invalid response") from exc
    text = value.get("text") if isinstance(value, dict) else None
    if not isinstance(text, str) or not text.strip():
        raise SourceIngestError("OCR service did not return readable text")
    if len(text.encode("utf-8")) > MAX_OCR_TEXT_BYTES:
        raise SourceIngestError("OCR service response exceeds the text limit")
    return text


def _extract_pdf_with_command_ocr(content: bytes, settings: OcrSettings) -> str:
    with tempfile.TemporaryDirectory(prefix="mediaforge-ocr-") as directory:
        root = Path(directory)
        source_path = root / "source.pdf"
        output_path = root / "output.txt"
        source_path.write_bytes(content)
        command = [
            part.replace("{input}", str(source_path)).replace("{output}", str(output_path))
            for part in settings.command
        ]
        try:
            completed = subprocess.run(
                command,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=settings.timeout_seconds,
                check=False,
                shell=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise SourceIngestError("local OCR command did not complete") from exc
        if completed.returncode != 0 or not output_path.is_file():
            raise SourceIngestError("local OCR command did not return readable text")
        raw = output_path.read_bytes()
    if len(raw) > MAX_OCR_TEXT_BYTES:
        raise SourceIngestError("local OCR output exceeds the text limit")
    return _decode_text(raw)


def _split_chapters(text: str, fallback_title: str) -> list[ParsedSourceChapter]:
    clean = text.replace("\r\n", "\n").replace("\r", "\n").strip()
    if not clean:
        raise SourceIngestError("source document contains no extractable text")
    headings = re.compile(r"^\s*(?:#{1,6}\s+.+|第\s*[0-9一二三四五六七八九十百千零〇]+\s*(?:章|节).*)\s*$")
    parsed: list[tuple[str, list[str]]] = []
    current_title = fallback_title.strip() or "未命名章节"
    current_lines: list[str] = []
    for line in clean.split("\n"):
        if headings.match(line):
            if current_lines:
                parsed.append((current_title, current_lines))
            current_title = line.lstrip("#").strip()[:240]
            current_lines = []
        else:
            current_lines.append(line)
    if current_lines:
        parsed.append((current_title, current_lines))
    if not parsed:
        parsed = [(current_title, [clean])]

    chapters: list[ParsedSourceChapter] = []
    for title, lines in parsed:
        body = "\n".join(lines).strip()
        if not body:
            continue
        for part_index, part in enumerate(_split_long_text(body), start=1):
            part_title = title if part_index == 1 else f"{title}（续 {part_index}）"
            chapters.append(
                ParsedSourceChapter(
                    title=part_title[:240],
                    content=part,
                    locator=f"{title} / 段 {part_index}",
                )
            )
    if not chapters:
        raise SourceIngestError("source document contains no chapter content")
    return chapters


def _split_long_text(text: str) -> list[str]:
    if len(text) <= MAX_CHAPTER_CHARS:
        return [text]
    pieces = []
    remaining = text
    while remaining:
        if len(remaining) <= MAX_CHAPTER_CHARS:
            pieces.append(remaining.strip())
            break
        boundary = remaining.rfind("\n", 0, MAX_CHAPTER_CHARS)
        if boundary < MAX_CHAPTER_CHARS // 2:
            boundary = remaining.rfind("。", 0, MAX_CHAPTER_CHARS)
        if boundary < MAX_CHAPTER_CHARS // 2:
            boundary = MAX_CHAPTER_CHARS
        pieces.append(remaining[:boundary].strip())
        remaining = remaining[boundary:].lstrip()
    return [piece for piece in pieces if piece]
