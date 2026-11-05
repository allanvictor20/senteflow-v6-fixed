"""
SenteFlow AI — Language Pipeline (v8)
======================================
Every owner message passes through normalize_business_text() before the AI
reads it:

  1. Which language?  Local keyword hints first (free, instant); otherwise
     Sunbird's language identification when SUNBIRD_API_KEY is set.
  2. Not English?     Sunbird translates it to English.
  3. The extractor gets BOTH the original and the English translation, and is
     told the translation is machine-made: names and amounts are checked
     against the original, so a translation slip can't silently change a
     number.

translate_reply() does the reverse for owners who want replies in their own
language (Settings → Business profile → Reply language). A translated reply is
only used if every number in the English reply survives translation;
otherwise the English reply is sent.

Without SUNBIRD_API_KEY nothing is translated: the AI reads the original text
(it copes with Swahili and simple Luganda, less well with longer Luganda).

History (v5, IDEA 12 — Luganda detection vocabulary gap):

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
import re
from typing import Any

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
        "omubare", "ensimbi", "obulemu", "amawulire",
        # Greetings and requests
        "nkuwe", "nkubuuza", "mpeereza", "nkufunire", "sirina", "yali",
        # Pronouns and connectors
        "ye", "bo", "mu", "ku", "wa", "oyo", "bano",
    ),
    "runyankole": (
        "nyine", "esente", "omuguzi", "nashashura",
        "nkuha", "obushomezi", "obuhango", "omuntu",
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
        "bedo", "biro", "camo", "dano",
        "ngo", "welo",
    ),
    "swahili": (
        "pesa", "kulipa", "mkopo", "mteja", "nitalipa",
        "fedha", "biashara", "bei", "malipo", "deni",
        "tafadhali", "asante", "habari", "sijui", "kesho",
        "jana", "shilingi", "benki", "akaunti",
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




# ── v8: Sunbird-backed normalisation ─────────────────────────────────────────

# Sunbird's language identification must be at least this sure before a
# message is treated as non-English (short English messages like "Brian paid
# 50k" are otherwise occasionally tagged as a local language).
SUNBIRD_MIN_CONFIDENCE = 0.6


def _package(text: str, english: str, code: str, provider: str, source: str, **extra) -> dict[str, Any]:
    from services.llm.sunbird import LANGUAGE_NAMES

    return {
        "original_text": text,
        "english_text": english,
        "language": code,
        "language_name": LANGUAGE_NAMES.get(code, code),
        "translated": english != text,
        "provider": provider,
        "detection_source": source,
        # v5 keys, kept for older callers
        "translated_text": english,
        "normalized_text": english,
        "original_language": code,
        **extra,
    }


async def detect_language_code(text: str) -> tuple[str, str]:
    """(ISO 639-3 code, how it was detected). "eng" when English or unsure."""
    from services.llm import sunbird

    keyword = detect_language(text)
    if keyword["source"] == "keyword":
        return sunbird.NAME_TO_CODE.get(keyword["language"], "eng"), "keyword"

    if sunbird.is_configured():
        try:
            code, confidence = await sunbird.identify_language(text)
        except sunbird.SunbirdError as exc:
            logger.warning("sunbird_language_id_failed", extra={"error": str(exc)})
            return "eng", "default"
        if code and code != "eng" and (confidence or 0) >= SUNBIRD_MIN_CONFIDENCE:
            return code, "sunbird"
    return "eng", "default"


async def normalize_business_text(text: str) -> dict[str, Any]:
    """
    Language-normalise an owner message for the extractor. Never raises:
    on any failure the original text is passed through untouched.
    """
    from services.llm import sunbird

    text = text or ""
    if not text.strip():
        return _package(text, text, "eng", "none", "default")

    code, source = await detect_language_code(text)
    if code == "eng":
        return _package(text, text, "eng", "none", source)

    if not sunbird.is_configured():
        return _package(text, text, code, "not-configured", source)

    try:
        english = await sunbird.translate(text, "eng", source_language=code)
    except sunbird.SunbirdError as exc:
        logger.warning("sunbird_translate_failed", extra={"error": str(exc), "language": code})
        return _package(text, text, code, "sunbird-failed", source, warning=str(exc))

    logger.info("message_translated", extra={"language": code, "detected_by": source})
    return _package(text, english, code, "sunbird", source)


_NUMBER_RE = re.compile(r"\d[\d,.]*\d|\d")
_FOOTNOTE_RE = re.compile(r"\n?_\(confidence: \d+%\)_\s*$")


def _numbers(text: str) -> list[str]:
    return [n.replace(",", "").rstrip(".") for n in _NUMBER_RE.findall(text or "")]


async def translate_reply(text: str, language: str | None) -> str | None:
    """
    The English reply in the owner's language, or None to send English.
    None whenever Sunbird is unavailable or the translation lost a number.
    """
    from services.llm import sunbird

    if not text or not language or language == "eng" or not sunbird.is_configured():
        return None

    footnote_match = _FOOTNOTE_RE.search(text)
    footnote = footnote_match.group(0) if footnote_match else ""
    body = text[: footnote_match.start()] if footnote_match else text

    try:
        translated = await sunbird.translate(body, language, source_language="eng")
    except sunbird.SunbirdError as exc:
        logger.warning("sunbird_reply_translate_failed", extra={"error": str(exc), "language": language})
        return None

    wanted = _numbers(body)
    got = _numbers(translated)
    for number in wanted:
        if number in got:
            got.remove(number)
        else:
            logger.warning("reply_translation_dropped_number", extra={"language": language, "number": number})
            return None
    return translated + footnote
