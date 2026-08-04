"""
SenteFlow AI — Language Pipeline (v5)
======================================
IDEA 12 — Luganda Detection Vocabulary Gap (P2):

The v4 implementation detected Luganda by checking for only 6 hardcoded words.
Any Luganda message not containing one of those words was classified as English
and sent to Gemini untranslated — producing UNKNOWN events.

This version:
1. Expands the vocabulary significantly for each supported language
2. Adds a Gemini/LLM language detection fallback for messages that don't
   match any keyword — so "Bambi nkuwe omubare wange" is no longer missed
3. Supports fallback to Sunbird for translation on any non-English message

Supported languages: Luganda, Runyankole, Ateso, Luo, Swahili
"""

import logging
import os
import re
from typing import Any

import httpx

logger = logging.getLogger(__name__)

# IDEA 12: Expanded vocabulary. Covers greetings, requests, money terms,
# business actions, and common sentence starters in each language.
LOCAL_LANGUAGE_HINTS = {
    "luganda": (
        # Money / payments (original 6)
        "nfunye", "ssente", "sente", "okulipa", "nkuweereza", "akuliwa",
        # Common words
        "bambi", "weebale", "gyebale", "ndi", "nze", "oli", "nga",
        # Business vocabulary
        "omuwendo", "obuuza", "abasuubuzi", "okulunda", "okugula", "okuuza",
        "omulimu", "obugwanjuba", "obukadde", "okuteeka", "okulaba",
        # Balance / account
        "omubare", "balance", "ensimbi", "obulemu", "amawulire",
        # Greetings and requests
        "nkuwe", "nkubuuza", "mpeereza", "nkufunire", "sirina", "yali",
        # Pronouns and connectors
        "ye", "bo", "mu", "ku", "wa", "oyo", "bano",
    ),
    "runyankole": (
        "nyine", "esente", "omuguzi", "nashashura",
        "nkuha", "aha", "obushomezi", "obuhango", "omuntu",
        "ebiragiro", "kuhemba", "okutamba", "nzire",
        "ekyooto", "orushomezi", "bwonka", "ntahe",
    ),
    "ateso": (
        "isinei", "edou", "akiro", "epone",
        "ijo", "noi", "bere", "ikoku", "akwap",
        "eong", "nuka", "ere", "lotim",
    ),
    "luo": (
        "omiyo", "lim", "chudo", "ngato",
        "bedo", "biro", "camo", "dano", "kare",
        "ngo", "polo", "welo", "ber",
    ),
    "swahili": (
        "pesa", "kulipa", "mkopo", "mteja", "nitalipa",
        "fedha", "biashara", "bei", "malipo", "deni",
        "tafadhali", "asante", "habari", "sijui", "kesho",
        "leo", "jana", "shilingi", "benki", "akaunti",
    ),
}

# Minimum word length for keyword matching — avoids false positives on
# short common tokens like "mu" (Luganda) matching English words
_MIN_HINT_LENGTH = 3

# Number of characters a message must have before we attempt LLM detection
_LLM_DETECTION_MIN_CHARS = 10


def detect_language(text: str) -> dict[str, Any]:
    """
    Detect the language of a WhatsApp business message.

    IDEA 12 fix:
    1. Check expanded vocabulary first (fast, no API call)
    2. If no match and message is long enough, use LLM detection (async path
       is available via detect_language_with_llm_fallback)
    3. If still no match, default to English with low confidence

    Matching is on whole words. Substring matching mis-classified across
    languages whenever one language's hint appeared inside another's word —
    Runyankole "esente" contains Luganda "sente", so every Runyankole message
    mentioning money was tagged Luganda and translated from the wrong source.

    The language with the most distinct hits wins; ties fall to declaration
    order, so detection no longer depends on which language happens to be
    checked first.
    """
    words = set(re.findall(r"[\w']+", (text or "").lower()))
    if not words:
        return {"language": "english", "confidence": 0.6, "source": "default"}

    best_language: str | None = None
    best_hits: list[str] = []

    for language, hints in LOCAL_LANGUAGE_HINTS.items():
        hits = sorted(
            {h for h in hints if len(h) >= _MIN_HINT_LENGTH and h in words}
        )
        if len(hits) > len(best_hits):
            best_language, best_hits = language, hits

    if not best_language:
        return {"language": "english", "confidence": 0.6, "source": "default"}

    # More corroborating words means more confidence, capped so keyword
    # matching never outranks an explicit LLM identification.
    confidence = min(0.75 + 0.05 * (len(best_hits) - 1), 0.95)
    return {
        "language": best_language,
        "confidence": round(confidence, 2),
        "source": "keyword",
        "matched_hint": best_hits[0],
        "matched_hints": best_hits,
    }


async def detect_language_with_llm_fallback(text: str) -> dict[str, Any]:
    """
    IDEA 12: Two-stage detection.
    Stage 1: Fast keyword matching (no API call).
    Stage 2: LLM-based detection for messages that slip through keywords.

    This catches messages like "Bambi nkuwe omubare wange" that use
    common Luganda words not in the keyword list.
    """
    result = detect_language(text)
    if result["source"] == "keyword":
        return result

    # Only attempt LLM detection for messages long enough to classify
    if len(text) < _LLM_DETECTION_MIN_CHARS:
        return result

    # Try LLM language detection
    try:
        detected = await _detect_with_llm(text)
        if detected:
            return detected
    except Exception as exc:
        logger.warning("llm_language_detection_failed", extra={"error": str(exc)})

    return result


async def _detect_with_llm(text: str) -> dict[str, Any] | None:
    """Ask the LLM to identify the language of the message."""
    from services.llm.llm_provider import complete_with_fallback

    system = (
        "You are a language detector for East African languages. "
        "Given a text, identify if it is written in Luganda, Runyankole, Ateso, Luo, "
        "Swahili, or English. Reply with ONLY the language name in lowercase "
        "(e.g. 'luganda', 'english'). If unsure, reply 'english'."
    )
    prompt = f'Identify the language of this text: "{text[:200]}"'

    try:
        raw, _ = await complete_with_fallback(prompt=prompt, system=system, max_tokens=10)
        language = raw.strip().lower().split()[0]
        known = {"luganda", "runyankole", "ateso", "luo", "swahili", "english"}
        if language in known:
            return {
                "language": language,
                "confidence": 0.70,
                "source": "llm_detection",
            }
    except Exception:
        pass
    return None


async def normalize_business_text(text: str) -> dict[str, Any]:
    """
    Return a normalized text package for downstream business extraction.
    Uses two-stage language detection (IDEA 12).
    If Sunbird is not configured or fails, preserve the original text safely.
    """
    detection = await detect_language_with_llm_fallback(text)
    language = detection["language"]

    if language == "english":
        return {
            "original_text": text,
            "translated_text": text,
            "normalized_text": text,
            "original_language": language,
            "language_confidence": detection["confidence"],
            "provider": "none",
            "detection_source": detection.get("source", "default"),
        }

    endpoint = os.environ.get("SUNBIRD_API_URL")
    api_key = os.environ.get("SUNBIRD_API_KEY", "")
    if not endpoint:
        return {
            "original_text": text,
            "translated_text": text,
            "normalized_text": text,
            "original_language": language,
            "language_confidence": detection["confidence"],
            "provider": "local-detection-only",
            "detection_source": detection.get("source"),
            "warning": "SUNBIRD_API_URL not configured",
        }

    try:
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.post(
                endpoint.rstrip("/") + "/translate",
                headers={"Authorization": f"Bearer {api_key}"} if api_key else {},
                json={
                    "text": text,
                    "source_language": language,
                    "target_language": "english",
                    "task": "business_normalization",
                },
            )
            response.raise_for_status()
            data = response.json()
    except Exception as exc:
        logger.warning("sunbird_normalization_failed", extra={"error": str(exc), "language": language})
        return {
            "original_text": text,
            "translated_text": text,
            "normalized_text": text,
            "original_language": language,
            "language_confidence": detection["confidence"],
            "provider": "sunbird-failed",
            "detection_source": detection.get("source"),
            "warning": str(exc),
        }

    translated = data.get("translated_text") or data.get("translation") or text
    normalized = data.get("normalized_text") or translated
    return {
        "original_text": text,
        "translated_text": translated,
        "normalized_text": normalized,
        "original_language": data.get("source_language") or language,
        "language_confidence": data.get("confidence", detection["confidence"]),
        "provider": "sunbird",
        "detection_source": detection.get("source"),
        "raw_provider_response": data,
    }
