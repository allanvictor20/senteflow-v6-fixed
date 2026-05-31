"""
SenteFlow AI — Intent Detector
================================
Classifies incoming text messages to determine what the user wants.
Uses a fast keyword-first approach, falling back to Gemini for ambiguous cases.

Intent categories:
  GREETING        — hello, hi, how are you
  TRANSACTION     — payment/expense/income records
  PAYMENT         — explicit payment records
  DEBT            — alias for transactions with debt context
  DEBT_QUERY      — who owes what
  SUMMARY_REQUEST — financial overview
  RECENT_REQUEST  — list recent transactions
  HELP            — what can you do
  UNKNOWN         — couldn't determine intent
"""

import logging
import os
import re
from enum import Enum
from typing import Optional

logger = logging.getLogger(__name__)


class Intent(str, Enum):
    GREETING = "greeting"
    TRANSACTION = "transaction"
    PAYMENT = "payment"
    DEBT = "debt"
    DEBT_QUERY = "debt_query"
    SUMMARY_REQUEST = "summary_request"
    RECENT_REQUEST = "recent_request"
    HELP = "help"
    UNKNOWN = "unknown"


# ─── Keyword Patterns ─────────────────────────────────────────────────────────
# Ordered from most specific to least specific

_PATTERNS: list[tuple[Intent, list[str]]] = [
    (Intent.GREETING, [
        r"\b(hello|hi|hey|good morning|good afternoon|good evening|sawa|habari|nkwagala)\b",
    ]),
    (Intent.SUMMARY_REQUEST, [
        r"\b(summary|balance|overview|total|totals|report|how much|finances)\b",
    ]),
    (Intent.RECENT_REQUEST, [
        r"\b(recent|latest|last|history|transactions|records|show me)\b",
    ]),
    (Intent.DEBT_QUERY, [
        r"\b(debt|owes|owe|outstanding|who paid|unpaid|balance for|dues)\b",
    ]),
    (Intent.HELP, [
        r"\b(help|how|what can|guide|instructions|tutorial|usage)\b",
    ]),
    (Intent.PAYMENT, [
        r"\b(paid|pay|payment|received|collected|deposited|banked)\b",
        r"\b(momo|mobile money|airtel|mtn|mpesa)\b",
    ]),
    (Intent.TRANSACTION, [
        r"\b(bought|purchased|sold|expense|income|spent|cost|fee|fine|loan|repaid|withdraw)\b",
        r"\b\d+(k|,000|shillings|shs|ugx|kes)\b",
    ]),
]


async def detect_intent(text: str) -> Intent:
    """
    Detect the intent of a text message.
    Fast keyword matching first; Gemini fallback for ambiguous cases.
    """
    if not text or not text.strip():
        return Intent.UNKNOWN

    normalized = text.lower().strip()

    # Try keyword patterns first (fast, zero API cost)
    for intent, patterns in _PATTERNS:
        for pattern in patterns:
            if re.search(pattern, normalized):
                logger.debug("intent_keyword_match", extra={"intent": intent.value, "pattern": pattern})
                return intent

    # Fallback to Gemini for ambiguous messages
    try:
        return await _gemini_intent_fallback(text)
    except Exception as e:
        logger.warning("gemini_intent_fallback_failed", extra={"error": str(e)})
        return Intent.UNKNOWN


async def _gemini_intent_fallback(text: str) -> Intent:
    """Use Gemini to classify intent when keyword matching fails."""
    from google import genai
    from google.genai.types import GenerateContentConfig

    client = genai.Client(api_key=os.environ.get("GEMINI_API_KEY"))

    prompt = f"""Classify this WhatsApp message from a business user in Uganda.
Return ONLY one of these labels (no explanation):
- greeting
- transaction  (recording a payment/expense/income)
- payment      (someone paid something)
- debt_query   (asking who owes money)
- summary_request (asking for financial summary)
- recent_request (asking for recent transactions)
- help         (asking how to use the system)
- unknown

Message: "{text}"

Label:"""

    response = client.models.generate_content(
        model="gemini-2.0-flash",
        contents=prompt,
        config=GenerateContentConfig(max_output_tokens=20, temperature=0.1),
    )

    raw = response.text.strip().lower()
    try:
        return Intent(raw)
    except ValueError:
        # Map partial matches
        for intent in Intent:
            if intent.value in raw:
                return intent
        return Intent.UNKNOWN
