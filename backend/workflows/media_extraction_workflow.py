"""
SenteFlow AI — Media Extraction Workflow
==========================================
Turns a saved media file (receipt photo, voice note, PDF, text) into an
ExtractionResult plus the session id that groups everything extracted from it.

This is the file-oriented counterpart to event_extraction_workflow, which works
on already-transcribed text. Both the WhatsApp media path and the /extract HTTP
route call in here so they produce identically shaped results.

The AI calls in ai/extractor are synchronous (the google-genai client blocks),
so this workflow is synchronous too. Async callers should hand it to
`asyncio.to_thread` rather than awaiting it.
"""

import logging
import mimetypes
import os
import time
import uuid
from typing import Optional

from core.errors import ExtractionError, UnsupportedFileTypeError
from domain.models import ExtractionResult

logger = logging.getLogger(__name__)

_IMAGE_MIMES = {"image/jpeg", "image/png", "image/webp", "image/gif", "image/heic"}
_AUDIO_MIMES = {"audio/ogg", "audio/mpeg", "audio/mp4", "audio/wav", "audio/x-wav", "audio/webm"}
_PDF_MIMES = {"application/pdf"}
_TEXT_MIMES = {"text/plain", "text/csv"}

# Extensions Evolution API sometimes hands us without a usable mime type.
_EXT_FALLBACK = {
    ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
    ".webp": "image/webp", ".gif": "image/gif",
    ".ogg": "audio/ogg", ".oga": "audio/ogg", ".mp3": "audio/mpeg",
    ".m4a": "audio/mp4", ".wav": "audio/wav",
    ".pdf": "application/pdf", ".txt": "text/plain", ".csv": "text/csv",
}


def _detect_mime(file_path: str, file_name: str) -> str:
    for candidate in (file_name, file_path):
        guessed, _ = mimetypes.guess_type(candidate or "")
        if guessed:
            return guessed
    ext = os.path.splitext(file_name or file_path or "")[1].lower()
    return _EXT_FALLBACK.get(ext, "application/octet-stream")


def _classify(mime_type: str) -> str:
    if mime_type in _IMAGE_MIMES or mime_type.startswith("image/"):
        return "image"
    if mime_type in _AUDIO_MIMES or mime_type.startswith("audio/"):
        return "audio"
    if mime_type in _PDF_MIMES:
        return "pdf"
    if mime_type in _TEXT_MIMES or mime_type.startswith("text/"):
        return "text"
    raise UnsupportedFileTypeError(mime_type)


def _summarise(result: ExtractionResult, input_type: str) -> str:
    count = len(result.transactions)
    if count == 0:
        return f"No transactions found in the {input_type}."
    total = sum(t.amount for t in result.transactions)
    currency = result.transactions[0].currency or "UGX"
    return f"{count} transaction{'s' if count != 1 else ''} totalling {currency} {total:,.0f}."


def _mean_confidence(result: ExtractionResult) -> float:
    scores = [
        t.field_confidence.mean
        for t in result.transactions
        if t.field_confidence is not None
    ]
    return round(sum(scores) / len(scores), 3) if scores else 0.0


def run_media_extraction(
    file_path: str,
    file_name: Optional[str] = None,
    invoice_prompt: Optional[str] = None,
) -> tuple[ExtractionResult, str]:
    """
    Extract transactions from a media file on disk.

    Args:
        file_path:      Local path to the downloaded/uploaded file.
        file_name:      Original filename — drives mime detection and provenance.
        invoice_prompt: Optional extra instruction appended for invoice-style
                        documents (used by the /extract upload route).

    Returns:
        (result, session_id)

    Raises:
        ExtractionError:         the file is missing/empty or the AI call failed.
        UnsupportedFileTypeError: the mime type has no extractor.
    """
    session_id = str(uuid.uuid4())
    display_name = file_name or os.path.basename(file_path)

    if not file_path or not os.path.exists(file_path):
        raise ExtractionError(f"Media file not found: {file_path}")

    file_bytes = None
    mime_type = _detect_mime(file_path, display_name)
    input_type = _classify(mime_type)

    started = time.perf_counter()
    try:
        if input_type == "text":
            with open(file_path, "r", encoding="utf-8", errors="replace") as fh:
                text_content = fh.read()
            if not text_content.strip():
                raise ExtractionError("Text file is empty")
            from ai.extractor import extract_from_text
            result = extract_from_text(text_content, session_id, display_name)
        else:
            with open(file_path, "rb") as fh:
                file_bytes = fh.read()
            if not file_bytes:
                raise ExtractionError("Media file is empty")

            if input_type == "image":
                from ai.extractor import extract_from_image
                result = extract_from_image(file_bytes, mime_type, session_id, display_name)
            elif input_type == "audio":
                from ai.extractor import extract_from_audio
                result = extract_from_audio(file_bytes, mime_type, session_id, display_name)
            else:
                from ai.extractor import extract_from_pdf
                result = extract_from_pdf(file_bytes, session_id, display_name)
    except (ExtractionError, UnsupportedFileTypeError):
        raise
    except Exception as exc:
        logger.error(
            "media_extraction_failed",
            extra={"file": display_name, "input_type": input_type, "error": str(exc)},
        )
        raise ExtractionError(f"Could not extract from {display_name}: {exc}") from exc

    if result is None:
        raise ExtractionError("The extraction model returned no result")

    result.session_id = session_id
    result.upload_session_id = session_id
    result.input_type = input_type
    result.processing_time_ms = round((time.perf_counter() - started) * 1000, 1)
    result.confidence = _mean_confidence(result)
    if not result.summary:
        result.summary = _summarise(result, input_type)

    if invoice_prompt:
        result.anomalies.append(f"Invoice hint applied: {invoice_prompt[:120]}")

    logger.info(
        "media_extraction_complete",
        extra={
            "session_id": session_id,
            "input_type": input_type,
            "transactions": len(result.transactions),
            "confidence": result.confidence,
        },
    )
    return result, session_id
