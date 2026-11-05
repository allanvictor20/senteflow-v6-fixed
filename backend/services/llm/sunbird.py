"""
SenteFlow AI — Sunbird AI client
================================
Sunbird AI (https://docs.sunbird.ai) builds speech and translation models for
Ugandan and other African languages. SenteFlow uses three of its endpoints:

  POST /tasks/language_id            which language is this text?   (3–200 chars)
  POST /tasks/translate              text → English (and back for replies)
  POST /tasks/audio/transcriptions   voice note → text, in a given language

Configuration:
  SUNBIRD_API_KEY   the access token from POST /auth/token (required to enable)
  SUNBIRD_API_URL   default https://api.sunbird.ai
  SUNBIRD_TIMEOUT   seconds, default 30 (models can take a while to wake up)

Every call raises SunbirdError on failure; callers decide how to fall back.
"""

from __future__ import annotations

import logging
import os
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

DEFAULT_BASE_URL = "https://api.sunbird.ai"

# Language codes SenteFlow offers in Settings. Sunbird uses ISO 639-3.
LANGUAGE_NAMES: dict[str, str] = {
    "eng": "English",
    "lug": "Luganda",
    "swa": "Swahili",
    "nyn": "Runyankole",
    "cgg": "Rukiga",
    "xog": "Lusoga",
    "ach": "Acholi",
    "teo": "Ateso",
    "lgg": "Lugbara",
    "laj": "Lango",
    "kin": "Kinyarwanda",
}

# Names used by the older keyword detector → ISO codes.
NAME_TO_CODE = {name.lower(): code for code, name in LANGUAGE_NAMES.items()}
NAME_TO_CODE.update({"luo": "ach"})


class SunbirdError(RuntimeError):
    pass


def is_configured() -> bool:
    return bool(os.environ.get("SUNBIRD_API_KEY", "").strip())


def _base_url() -> str:
    return (os.environ.get("SUNBIRD_API_URL") or DEFAULT_BASE_URL).rstrip("/")


def _timeout() -> float:
    try:
        return float(os.environ.get("SUNBIRD_TIMEOUT", "30"))
    except ValueError:
        return 30.0


def _headers() -> dict:
    key = os.environ.get("SUNBIRD_API_KEY", "").strip()
    if not key:
        raise SunbirdError("SUNBIRD_API_KEY not set")
    return {"Authorization": f"Bearer {key}"}


async def _post(path: str, **kwargs) -> dict:
    url = _base_url() + path
    try:
        async with httpx.AsyncClient(timeout=_timeout()) as client:
            response = await client.post(url, headers=_headers(), **kwargs)
    except httpx.HTTPError as exc:
        raise SunbirdError(f"{path}: {type(exc).__name__}: {exc}") from exc
    if response.status_code >= 400:
        raise SunbirdError(f"{path}: HTTP {response.status_code}: {response.text[:200]}")
    try:
        data = response.json()
    except ValueError as exc:
        raise SunbirdError(f"{path}: response was not JSON") from exc
    if not isinstance(data, dict):
        raise SunbirdError(f"{path}: unexpected response shape")
    return data


async def identify_language(text: str) -> tuple[Optional[str], Optional[float]]:
    """
    (ISO code, confidence) for the text, or (None, None) when Sunbird can't
    tell. Only the first 200 characters are sent (the endpoint's limit).
    """
    sample = (text or "").strip()[:200]
    if len(sample) < 3:
        return None, None
    data = await _post("/tasks/language_id", json={"text": sample})
    code = str(data.get("language") or "").strip().lower()
    confidence = data.get("confidence")
    if not code or " " in code or confidence is None:   # "language not detected"
        return None, None
    try:
        return code, float(confidence)
    except (TypeError, ValueError):
        return code, None


async def translate(text: str, target_language: str, source_language: Optional[str] = None) -> str:
    """Translate text. Sunbird auto-detects the source when it isn't given."""
    if source_language and source_language == target_language:
        return text
    payload = {"text": text, "target_language": target_language}
    if source_language:
        payload["source_language"] = source_language
    data = await _post("/tasks/translate", json=payload)
    output = data.get("output") if isinstance(data.get("output"), dict) else data
    translated = (output or {}).get("translated_text") or (output or {}).get("translation")
    if not isinstance(translated, str) or not translated.strip():
        raise SunbirdError("/tasks/translate: empty translation")
    return translated.strip()


async def transcribe(audio_bytes: bytes, mime_type: Optional[str], language: str) -> str:
    """Speech to text. Sunbird asks for the language explicitly (auto-detect is unreliable)."""
    mime = (mime_type or "audio/ogg").split(";")[0].strip() or "audio/ogg"
    ext = {
        "audio/ogg": "ogg", "audio/mpeg": "mp3", "audio/mp3": "mp3", "audio/wav": "wav",
        "audio/x-wav": "wav", "audio/mp4": "m4a", "audio/m4a": "m4a", "audio/aac": "aac",
        "audio/webm": "webm",
    }.get(mime, "ogg")
    data = await _post(
        "/tasks/audio/transcriptions",
        files={"audio": (f"voice.{ext}", audio_bytes, mime)},
        data={"language": language},
    )
    text = data.get("audio_transcription")
    return text.strip() if isinstance(text, str) else ""
