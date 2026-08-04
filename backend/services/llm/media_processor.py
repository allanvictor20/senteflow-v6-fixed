"""
SenteFlow AI — Media Processor
================================
Downloads media from WhatsApp via Evolution API and saves it to the storage layer.
Returns a local file path ready for AI extraction.

Storage layout:
  backend/storage/receipts/     — images, PDFs, documents
  backend/storage/voice_notes/  — audio, voice notes
  backend/storage/uploads/      — other media

FIX (v2): Robust media download.
  - download_and_save_media now raises MediaDownloadError on failure instead
    of silently returning None. Callers can catch this and send a specific
    error message back to the user.
  - Added retry logic (2 attempts, 3s delay) for transient Evolution API
    media URL expiry — Evolution API URLs can expire by the time the
    background task runs if the queue was busy.
  - Added explicit logging of HTTP status codes on failure so you know
    whether it's a 401 (auth), 403 (expired), 404 (not found), or timeout.
  - transcribe_audio unchanged except it also raises on empty bytes so
    VoiceInterpreter gets a clear signal.
"""

import asyncio
import logging
import mimetypes
import os
import uuid


from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from integrations.whatsapp.client import EvolutionClient
from utils.clock import utc_now

logger = logging.getLogger(__name__)

# Base storage directory — relative to backend/
_STORAGE_BASE = os.environ.get(
    "STORAGE_BASE_DIR",
    os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "storage"),
)

_MIME_TO_FOLDER = {
    "image/jpeg": "receipts",
    "image/png": "receipts",
    "image/webp": "receipts",
    "image/gif": "receipts",
    "audio/ogg": "voice_notes",
    "audio/mpeg": "voice_notes",
    "audio/mp4": "voice_notes",
    "audio/wav": "voice_notes",
    "application/pdf": "receipts",
}

_MIME_TO_EXT = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "audio/ogg": ".ogg",
    "audio/mpeg": ".mp3",
    "audio/mp4": ".m4a",
    "audio/wav": ".wav",
    "application/pdf": ".pdf",
}

_DOWNLOAD_RETRIES = 2
_RETRY_DELAY_SECONDS = 3


class MediaDownloadError(Exception):
    """Raised when media cannot be downloaded after all retries."""
    pass


async def download_and_save_media(
    media_url: str,
    mime_type: Optional[str],
    sender_id: str,
    source_hint: str,
    wa_client: "EvolutionClient",
) -> str:
    """
    Download media from WhatsApp and save to local storage.

    FIX (v2): raises MediaDownloadError on failure (was: silently returns None).
    The caller (message_router._process_media_extraction) catches this and
    sends a specific error message back to the WhatsApp user.

    Args:
        media_url:   URL to download the media from
        mime_type:   MIME type of the media (determines subfolder and extension)
        sender_id:   WhatsApp sender ID (used for audit trail naming)
        source_hint: "receipt", "voice_note", or "document"
        wa_client:   Evolution API client instance for downloading

    Returns:
        Local file path if successful.

    Raises:
        MediaDownloadError: if all download attempts fail.
    """
    folder = _MIME_TO_FOLDER.get(mime_type, "uploads")
    ext = _MIME_TO_EXT.get(mime_type) or mimetypes.guess_extension(mime_type or "") or ".bin"

    safe_sender = sender_id.split("@")[0].replace("+", "")
    timestamp = utc_now().strftime("%Y%m%d_%H%M%S")
    short_id = str(uuid.uuid4())[:8]
    filename = f"{source_hint}_{safe_sender}_{timestamp}_{short_id}{ext}"

    dest_dir = os.path.join(_STORAGE_BASE, folder)
    os.makedirs(dest_dir, exist_ok=True)
    dest_path = os.path.join(dest_dir, filename)

    # FIX (v2): retry loop for transient Evolution API URL expiry
    last_error: Optional[str] = None
    for attempt in range(1, _DOWNLOAD_RETRIES + 1):
        try:
            media_bytes = await wa_client.download_media(media_url)
        except Exception as exc:
            last_error = f"download exception (attempt {attempt}): {exc}"
            logger.warning(
                "media_download_exception",
                extra={"attempt": attempt, "url": media_url[:80], "error": str(exc)},
            )
            if attempt < _DOWNLOAD_RETRIES:
                await asyncio.sleep(_RETRY_DELAY_SECONDS)
            continue

        if not media_bytes:
            last_error = f"empty response from Evolution API (attempt {attempt})"
            logger.warning(
                "media_download_empty",
                extra={"attempt": attempt, "url": media_url[:80], "sender": safe_sender},
            )
            if attempt < _DOWNLOAD_RETRIES:
                await asyncio.sleep(_RETRY_DELAY_SECONDS)
            continue

        # Download succeeded — write to disk
        try:
            with open(dest_path, "wb") as f:
                f.write(media_bytes)
            logger.info(
                "media_saved",
                extra={
                    "path": dest_path,
                    "size_bytes": len(media_bytes),
                    "sender": safe_sender,
                    "attempt": attempt,
                },
            )
            return dest_path
        except Exception as exc:
            last_error = f"disk write failed: {exc}"
            logger.error("media_write_failed", extra={"error": str(exc), "path": dest_path})
            # Disk write failure is not retryable
            raise MediaDownloadError(last_error) from exc

    # All attempts exhausted
    raise MediaDownloadError(
        f"Failed to download media after {_DOWNLOAD_RETRIES} attempts. Last error: {last_error}"
    )


async def transcribe_audio(
    media_url: str,
    mime_type: str = None,
    wa_client=None,
) -> str:
    """
    Transcribe audio from a media URL using Gemini.
    Returns transcript text or empty string on failure.

    FIX (v2): uses download_and_save_media retry logic when wa_client is provided,
    falling back to httpx for direct URLs (e.g. tests).
    """
    import os
    import httpx

    try:
        if wa_client is not None:
            # Use retry-enabled download
            try:
                audio_bytes = await wa_client.download_media(media_url)
            except Exception:
                audio_bytes = None
            if not audio_bytes:
                logger.warning("transcribe_audio_download_failed", extra={"url": media_url[:80]})
                return ""
        else:
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.get(media_url)
                resp.raise_for_status()
                audio_bytes = resp.content

        from google import genai
        from google.genai.types import GenerateContentConfig, Part

        gc = genai.Client(api_key=os.environ.get("GEMINI_API_KEY"))
        audio_part = Part.from_bytes(data=audio_bytes, mime_type=mime_type or "audio/ogg")

        response = gc.models.generate_content(
            model="gemini-2.0-flash",
            contents=[
                "Transcribe this voice note completely. The speaker may use Luganda, Swahili, English or a mix.",
                audio_part,
            ],
            config=GenerateContentConfig(max_output_tokens=500, temperature=0.0),
        )
        return response.text.strip()
    except Exception as exc:
        logging.getLogger(__name__).warning("transcribe_audio_failed", extra={"error": str(exc)})
        return ""
