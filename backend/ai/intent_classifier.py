"""Intent interpretation for the WhatsApp-native SME assistant."""

import json
import logging
import os
import re
from enum import Enum

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class Intent(str, Enum):
    # Customer-facing intents
    CUSTOMER_QUESTION = "customer_question"
    ORDER_REQUEST = "order_request"
    NEGOTIATION = "negotiation"
    PRODUCT_AVAILABILITY = "product_availability"
    COMPLAINT = "complaint"
    DELIVERY_UPDATE = "delivery_update"
    APPOINTMENT_REQUEST = "appointment_request"
    SUPPLIER_MESSAGE = "supplier_message"

    # Financial intents
    RECORD_PAYMENT = "record_payment"
    RECORD_DEBT = "record_debt"
    PAYMENT_PROMISE = "payment_promise"
    DEBT_SUMMARY = "debt_summary"
    ACTIVITY_SUMMARY = "activity_summary"

    # Memory/query intents
    FOLLOWUP_REQUEST = "followup_request"
    RECENT_ACTIVITY = "recent_activity"
    CUSTOMER_HISTORY = "customer_history"

    # System intents
    RECEIPT = "receipt"
    VOICE_NOTE = "voice_note"
    HELP = "help"
    CLARIFY = "clarify"
    UNKNOWN = "unknown"


class IntentResult(BaseModel):
    intent: Intent
    confidence: float = Field(0.0, ge=0.0, le=1.0)
    reason: str = ""


def _fast_classify(text: str, input_type: str):
    lowered = (text or "").lower().strip()

    if input_type in {"receipt", "image", "document", "pdf"}:
        return IntentResult(intent=Intent.RECEIPT, confidence=0.95, reason="media receipt/document")
    if input_type == "voice_note":
        return IntentResult(intent=Intent.VOICE_NOTE, confidence=0.95, reason="voice input")
    if not lowered:
        return IntentResult(intent=Intent.UNKNOWN, confidence=0.2, reason="empty message")

    if any(term in lowered for term in ("help", "what can you do", "start")):
        return IntentResult(intent=Intent.HELP, confidence=0.92, reason="help request")
    if any(term in lowered for term in ("who owes", "who still owes", "debts", "hasn't paid", "hasnt paid")):
        return IntentResult(intent=Intent.DEBT_SUMMARY, confidence=0.92, reason="debt question")
    if any(term in lowered for term in ("summarize", "summary", "today's activity", "activity today")):
        return IntentResult(intent=Intent.ACTIVITY_SUMMARY, confidence=0.90, reason="summary request")
    if any(term in lowered for term in ("recent", "last activity", "activity history", "last events")):
        return IntentResult(intent=Intent.RECENT_ACTIVITY, confidence=0.90, reason="recent activity request")
    if any(term in lowered for term in ("what do we know about", "customer history", "remember about")):
        return IntentResult(intent=Intent.CUSTOMER_HISTORY, confidence=0.88, reason="customer memory request")

    if any(term in lowered for term in (
        "do you have", "is there", "available", "in stock", "have cement",
        "still have", "sold out",
    )):
        return IntentResult(intent=Intent.PRODUCT_AVAILABILITY, confidence=0.90, reason="stock availability question")

    if any(term in lowered for term in (
        "i want", "i need", "send me", "bring me", "order", "i'll take", "ill take",
    )):
        return IntentResult(intent=Intent.ORDER_REQUEST, confidence=0.88, reason="order request")

    if any(term in lowered for term in (
        "can you reduce", "discount", "best price", "negotiate",
        "too expensive", "lower the price",
    )):
        return IntentResult(intent=Intent.NEGOTIATION, confidence=0.90, reason="price negotiation")

    if any(term in lowered for term in (
        "i'll pay", "ill pay", "will pay", "pay friday", "pay tomorrow",
        "send money soon", "by end of week",
    )):
        return IntentResult(intent=Intent.PAYMENT_PROMISE, confidence=0.90, reason="payment promise")

    if any(term in lowered for term in ("remind me", "reminder", "don't forget", "dont forget", "follow up on")):
        return IntentResult(intent=Intent.FOLLOWUP_REQUEST, confidence=0.90, reason="follow-up request")

    if any(term in lowered for term in ("complaint", "wrong item", "not happy", "bad service", "damaged")):
        return IntentResult(intent=Intent.COMPLAINT, confidence=0.88, reason="customer complaint")
    if any(term in lowered for term in ("delivered", "delivery", "rider", "on the way", "arrived")):
        return IntentResult(intent=Intent.DELIVERY_UPDATE, confidence=0.86, reason="delivery update")
    if any(term in lowered for term in ("meeting", "appointment", "come tomorrow", "visit", "schedule")):
        return IntentResult(intent=Intent.APPOINTMENT_REQUEST, confidence=0.86, reason="appointment request")
    if any(term in lowered for term in ("supplier", "wholesaler", "restock", "new stock arriving")):
        return IntentResult(intent=Intent.SUPPLIER_MESSAGE, confidence=0.86, reason="supplier context")

    has_amount = bool(re.search(r"\b\d[\d,]*(?:\.\d+)?\s*(?:k|ugx|shs|/=)?\b", lowered))
    if has_amount:
        if any(term in lowered for term in ("owes me", "owes us", "still owes", "on credit", "mkopo")):
            return IntentResult(intent=Intent.RECORD_DEBT, confidence=0.88, reason="debt record with amount")
        if any(term in lowered for term in ("paid", "sent", "received", "akuliwa", "kulipa", "deposited")):
            return IntentResult(intent=Intent.RECORD_PAYMENT, confidence=0.88, reason="payment record with amount")

    if any(term in lowered for term in ("paid", "sent", "received", "bought", "sold", "spent", "deposited")):
        return IntentResult(intent=Intent.RECORD_PAYMENT, confidence=0.72, reason="financial action without amount")

    if "usual" in lowered or "same amount" in lowered or "same as" in lowered:
        return None

    return None


_GROQ_SYSTEM = (
    "You are an intent classifier for a WhatsApp business assistant used by SME owners.\n"
    "Classify the message into exactly one of these intents:\n"
    "customer_question, order_request, negotiation, product_availability, complaint,\n"
    "delivery_update, appointment_request, supplier_message, record_payment,\n"
    "record_debt, payment_promise, debt_summary, activity_summary,\n"
    "followup_request, recent_activity, customer_history, receipt, voice_note,\n"
    "help, clarify, unknown.\n\n"
    "Return ONLY valid JSON: {\"intent\": \"...\", \"confidence\": 0.0-1.0, \"reason\": \"one sentence\"}."
)


def _groq_client():
    """Build an async Groq-compatible OpenAI client."""
    from openai import AsyncOpenAI
    api_key = os.environ.get("GROQ_API_KEY")
    if not api_key:
        raise RuntimeError("GROQ_API_KEY not set")
    return AsyncOpenAI(
        api_key=api_key,
        base_url="https://api.groq.com/openai/v1",
    )


async def _groq_classify(text: str, input_type: str) -> IntentResult:
    try:
        client = _groq_client()
        model = os.environ.get("GROQ_MODEL", "llama-3.3-70b-versatile")
        user_prompt = f'Message: "{text}"\nInput type: {input_type}'
        response = await client.chat.completions.create(
            model=model,
            max_tokens=150,
            temperature=0.0,
            messages=[
                {"role": "system", "content": _GROQ_SYSTEM},
                {"role": "user", "content": user_prompt},
            ],
        )
        raw = (response.choices[0].message.content or "").strip()
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        data = json.loads(raw.strip())
        try:
            intent = Intent(data.get("intent", "unknown"))
        except ValueError:
            intent = Intent.UNKNOWN
        return IntentResult(
            intent=intent,
            confidence=float(data.get("confidence", 0.5)),
            reason=data.get("reason", "Groq classification"),
        )
    except Exception as exc:
        logger.warning("groq_intent_classify_failed", extra={"error": str(exc), "text": text[:60]})
        return IntentResult(intent=Intent.UNKNOWN, confidence=0.3, reason=f"Groq failed: {exc}")


# Backward-compatible alias
_gemini_classify = _groq_classify


async def classify_intent_async(text: str = "", input_type: str = "text") -> IntentResult:
    fast = _fast_classify(text, input_type)
    if fast is not None and fast.confidence >= 0.85:
        return fast
    return await _groq_classify(text, input_type)


def classify_intent(text: str = "", input_type: str = "text") -> IntentResult:
    fast = _fast_classify(text, input_type)
    if fast is not None:
        return fast
    return IntentResult(intent=Intent.UNKNOWN, confidence=0.35, reason="no strong local pattern")
